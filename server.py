"""
Krita MCP Server
Bridge between Claude (or any MCP client) and Krita painting application.

Uses FastMCP to expose Krita painting tools over the Model Context Protocol,
communicating with a Krita plugin via HTTP.
"""
import base64
from fastmcp import FastMCP
from fastmcp.utilities.types import Image
import httpx
import os
from typing import Optional

# Configuration
KRITA_URL = os.environ.get("KRITA_URL", "http://localhost:5678")

mcp = FastMCP("krita-mcp")


def send_command(action: str, params: dict = None, timeout: float = 30.0) -> dict:
    """Send command to Krita plugin and return result."""
    if params is None:
        params = {}

    try:
        response = httpx.post(
            KRITA_URL,
            json={"action": action, "params": params},
            timeout=timeout
        )
        return response.json()
    except httpx.ConnectError:
        return {"error": "Cannot connect to Krita. Is Krita running with the MCP plugin enabled?"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def krita_get_canvas_preview(max_dimension: int = 1024) -> Image:
    """
    Export the current canvas and return it as an inline image to close the vision loop.
    
    Args:
        max_dimension: Maximum width or height of the returned image. Reduces payload size for large canvases. Set to 0 for unscaled.
    """
    result = send_command("get_canvas_preview", {"max_dimension": max_dimension}, timeout=120.0)
    
    if "error" in result:
        raise Exception(f"Krita Error: {result['error']}")
        
    # krita_get_canvas_preview
    img_bytes = base64.b64decode(result["base64"])
    return Image(data=img_bytes, format="png")  # not "image/png"



@mcp.tool()
def krita_get_canvas_region(x: int, y: int, width: int, height: int) -> Image:
    """
    Export a specific cropped region of the canvas as an inline image.
    Useful for inspecting fine details without transmitting the entire canvas.
    
    Args:
        x: X coordinate of the top-left corner.
        y: Y coordinate of the top-left corner.
        width: Width of the region to inspect.
        height: Height of the region to inspect.
    """
    result = send_command("get_canvas_region", {
        "x": x, "y": y, "width": width, "height": height
    }, timeout=60.0)
    
    if "error" in result:
        raise Exception(f"Krita Error: {result['error']}")
        
    # krita_get_canvas_preview
    img_bytes = base64.b64decode(result["base64"])
    return Image(data=img_bytes, format="png")  # not "image/png"


@mcp.tool()
def krita_list_layers() -> str:
    """
    List all layers in the document including their name, visibility, opacity, and type.
    Use krita_get_layer_thumbnail(name) to visually inspect a specific layer's contents.
    """
    result = send_command("list_layers", {}, timeout=30.0)

    if "error" in result:
        return f"Error: {result['error']}"

    layers = result.get("layers", [])
    if not layers:
        return "No layers found."

    lines = ["Document Layers:"]
    for i, layer in enumerate(layers):
        status = "Visible" if layer["visible"] else "Hidden"
        opacity = int(layer["opacity"] / 255.0 * 100)
        lines.append(f"  {i}: {layer['name']} ({layer['type']}) - {status}, Opacity: {opacity}%")

    return "\n".join(lines)


@mcp.tool()
def krita_get_layer_thumbnail(name: str, size: int = 128) -> Image:
    """
    Get a visual thumbnail of one specific layer by name, so you can inspect a single
    layer's contents in isolation without it being composited with other layers.

    Args:
        name: Exact name of the layer to inspect (see krita_list_layers).
        size: Thumbnail width/height in pixels (default 128).
    """
    result = send_command("get_layer_thumbnail", {"name": name, "size": size}, timeout=30.0)

    if "error" in result:
        raise Exception(f"Krita Error: {result['error']}")

    img_bytes = base64.b64decode(result["base64"])
    return Image(data=img_bytes, format="png")


@mcp.tool()
def krita_draw_path(
    points: list[list[float]],
    is_bezier: bool = False,
    size: float = 5.0,
    color: str = "#ffffff",
    opacity: float = 1.0,
    blend_mode: str = "normal"
) -> str:
    """
    Draw a continuous path or Bezier curve with opacity and blend modes.
    
    Args:
        points: List of [x, y] coordinates. If is_bezier=True, sequence must be [start, ctrl1, ctrl2, end, ctrl1, ctrl2, end...].
        is_bezier: Interpret points as cubic Bezier segments.
        size: Stroke thickness.
        color: Hex color string.
        opacity: Stroke opacity (0.0 to 1.0).
        blend_mode: Composition mode (e.g., normal, multiply, screen, overlay).
    """
    result = send_command("draw_path", {
        "points": points, "is_bezier": is_bezier, "size": size,
        "color": color, "opacity": opacity, "blend_mode": blend_mode
    }, timeout=30.0)
    
    if "error" in result:
        raise Exception(f"Krita Error: {result['error']}")
    return "Path drawn successfully."


@mcp.tool()
def krita_fill_gradient(
    gradient_type: str,
    x1: float, y1: float, x2: float, y2: float,
    color_stops: list[dict],
    opacity: float = 1.0,
    blend_mode: str = "normal"
) -> str:
    """
    Fill the canvas with a linear or radial gradient.
    
    Args:
        gradient_type: "linear" or "radial".
        x1, y1: Start coordinate (or center for radial).
        x2, y2: End coordinate (or edge radius target for radial).
        color_stops: List of dictionaries formatting stops, e.g., [{"position": 0.0, "color": "#000000"}, {"position": 1.0, "color": "#ffffff"}].
        opacity: Fill opacity (0.0 to 1.0).
        blend_mode: Composition mode.
    """
    result = send_command("fill_gradient", {
        "type": gradient_type, "x1": x1, "y1": y1, "x2": x2, "y2": y2,
        "color_stops": color_stops, "opacity": opacity, "blend_mode": blend_mode
    }, timeout=30.0)
    
    if "error" in result:
        raise Exception(f"Krita Error: {result['error']}")
    return f"{gradient_type.capitalize()} gradient applied successfully."


@mcp.tool()
def krita_health() -> str:
    """Check if Krita is running and the MCP plugin is active."""
    try:
        response = httpx.get(f"{KRITA_URL}/health", timeout=5.0)
        data = response.json()
        return f"Krita is running. Plugin: {data.get('plugin', 'unknown')}"
    except:
        return "Cannot connect to Krita. Make sure Krita is running with the MCP plugin enabled."


@mcp.tool()
def krita_new_canvas(
    width: int = 800,
    height: int = 600,
    name: str = "New Canvas",
    background: str = "#1a1a2e"
) -> str:
    """
    Create a new canvas in Krita.

    Args:
        width: Canvas width in pixels (default 800)
        height: Canvas height in pixels (default 600)
        name: Document name
        background: Background color as hex (default dark blue)
    """
    result = send_command("new_canvas", {
        "width": width,
        "height": height,
        "name": name,
        "background": background
    })

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Created canvas: {width}x{height}, background: {background}"


@mcp.tool()
def krita_set_color(color: str) -> str:
    """
    Set the foreground (paint) color.

    Args:
        color: Hex color code (e.g., "#ff6b6b", "#b8a9c9")
    """
    result = send_command("set_color", {"color": color})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Color set to {color}"


@mcp.tool()
def krita_set_brush(
    preset: Optional[str] = None,
    size: Optional[int] = None,
    opacity: Optional[float] = None
) -> str:
    """
    Set brush preset and properties.

    Args:
        preset: Brush preset name (partial match, e.g., "Basic", "Soft", "Airbrush")
        size: Brush size in pixels
        opacity: Brush opacity (0.0 to 1.0)
    """
    params = {}
    if preset:
        params["preset"] = preset
    if size:
        params["size"] = size
    if opacity is not None:
        params["opacity"] = opacity

    result = send_command("set_brush", params)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Brush set: preset={preset}, size={size}, opacity={opacity}"


@mcp.tool()
def krita_stroke(
    points: list[list[int]],
    pressure: float = 1.0,
    opacity: Optional[float] = None,
    hardness: Optional[float] = None
) -> str:
    """
    Paint a stroke through a series of points.

    Args:
        points: List of [x, y] coordinate pairs, e.g., [[100, 100], [150, 120], [200, 150]]
        pressure: Brush pressure (0.0 to 1.0). Multiplies against opacity to lighten the stroke.
        opacity: Stroke opacity (0.0 to 1.0). Defaults to the opacity set via krita_set_brush.
        hardness: Brush edge hardness (0.0 soft to 1.0 hard). Defaults to 0.5.
    """
    if len(points) < 2:
        return "Error: Need at least 2 points for a stroke"

    params = {"points": points, "pressure": pressure}
    if opacity is not None:
        params["opacity"] = opacity
    if hardness is not None:
        params["hardness"] = hardness

    result = send_command("stroke", params)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Stroke painted with {len(points)} points"


@mcp.tool()
def krita_fill(x: int, y: int, radius: int = 50) -> str:
    """
    Fill an area with current color (paints a filled circle at the point).

    Args:
        x: X coordinate
        y: Y coordinate
        radius: Fill radius in pixels
    """
    result = send_command("fill", {"x": x, "y": y, "radius": radius})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Filled at ({x}, {y}) with radius {radius}"


@mcp.tool()
def krita_draw_shape(
    shape: str,
    x: int,
    y: int,
    width: int = 100,
    height: int = 100,
    fill: bool = True,
    stroke: bool = False,
    x2: Optional[int] = None,
    y2: Optional[int] = None
) -> str:
    """
    Draw a shape on the canvas.

    Args:
        shape: Type of shape - "rectangle", "ellipse", or "line"
        x: X coordinate (top-left for shapes, start point for lines)
        y: Y coordinate (top-left for shapes, start point for lines)
        width: Width of shape (ignored for lines if x2/y2 provided)
        height: Height of shape (ignored for lines if x2/y2 provided)
        fill: Whether to fill the shape
        stroke: Whether to draw outline
        x2: End X for lines (optional)
        y2: End Y for lines (optional)
    """
    params = {
        "shape": shape,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "fill": fill,
        "stroke": stroke
    }
    if x2 is not None:
        params["x2"] = x2
    if y2 is not None:
        params["y2"] = y2

    result = send_command("draw_shape", params)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Drew {shape} at ({x}, {y})"


@mcp.tool()
def krita_get_canvas(filename: str = "canvas.png") -> str:
    """
    Export current canvas to a PNG file and return the path.
    Use this to see your painting progress.

    Args:
        filename: Output filename (saved to configured output directory)
    """
    result = send_command("get_canvas", {"filename": filename}, timeout=120.0)

    if "error" in result:
        return f"Error: {result['error']}"

    path = result.get("path", "")
    return f"Canvas saved to: {path}"


@mcp.tool()
def krita_undo() -> str:
    """Undo the last action."""
    result = send_command("undo", {})

    if "error" in result:
        return f"Error: {result['error']}"
    return "Undone"


@mcp.tool()
def krita_redo() -> str:
    """Redo the last undone action."""
    result = send_command("redo", {})

    if "error" in result:
        return f"Error: {result['error']}"
    return "Redone"


@mcp.tool()
def krita_clear(color: str = "#1a1a2e") -> str:
    """
    Clear the canvas to a solid color.

    Args:
        color: Color to fill canvas with (default dark blue)
    """
    result = send_command("clear", {"color": color})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Canvas cleared to {color}"


@mcp.tool()
def krita_save(path: str) -> str:
    """
    Save the current canvas to a specific file path.

    Args:
        path: Full file path to save to (e.g., "C:/art/my_painting.png")
    """
    result = send_command("save", {"path": path}, timeout=120.0)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Saved to {path}"


@mcp.tool()
def krita_get_color_at(x: int, y: int) -> str:
    """
    Sample the color at a specific pixel (eyedropper).

    Args:
        x: X coordinate
        y: Y coordinate
    """
    result = send_command("get_color_at", {"x": x, "y": y})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Color at ({x}, {y}): {result.get('color', 'unknown')} (R:{result.get('r')}, G:{result.get('g')}, B:{result.get('b')})"


@mcp.tool()
def krita_list_brushes(filter: str = "", limit: int = 20) -> str:
    """
    List available brush presets.

    Args:
        filter: Filter brushes by name (partial match)
        limit: Maximum number to return
    """
    result = send_command("list_brushes", {"filter": filter, "limit": limit})

    if "error" in result:
        return f"Error: {result['error']}"

    brushes = result.get("brushes", [])
    if not brushes:
        return "No brushes found matching filter"

    return f"Available brushes ({len(brushes)}):\n" + "\n".join(f"  - {b}" for b in brushes)


@mcp.tool()
def krita_open_file(path: str) -> str:
    """
    Open an existing file in Krita (.kra, .png, .jpg, etc).

    Args:
        path: Full file path to open (e.g., "C:/art/my_painting.kra")
    """
    result = send_command("open_file", {"path": path}, timeout=30.0)

    if "error" in result:
        return f"Error: {result['error']}"

    return f"Opened: {result.get('name', 'unknown')} ({result.get('width')}x{result.get('height')})"

@mcp.tool()
def krita_add_layer(name: str = "Layer") -> str:
    """
    Add a new transparent paint layer above the current active layer, and make it active.

    Args:
        name: Name for the new layer
    """
    result = send_command("add_layer", {"name": name})
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Added layer: {result.get('name')}"


@mcp.tool()
def krita_set_active_layer(name: str) -> str:
    """
    Set the active layer by name.

    Args:
        name: Exact name of the layer to activate
    """
    result = send_command("set_active_layer", {"name": name})
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Active layer set to: {name}"


@mcp.tool()
def krita_delete_layer(name: str) -> str:
    """
    Delete a layer by name.

    Args:
        name: Exact name of the layer to delete
    """
    result = send_command("delete_layer", {"name": name})
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Deleted layer: {name}"


@mcp.tool()
def krita_clear_layer() -> str:
    """
    Clear the active layer to fully transparent (erases all content on this layer only,
    without affecting other layers).
    """
    result = send_command("clear_layer", {})
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Cleared layer: {result.get('layer')}"

if __name__ == "__main__":
    mcp.run()