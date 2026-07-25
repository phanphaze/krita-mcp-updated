"""
Krita MCP Bridge - HTTP server for external paint commands in Krita
Allows Claude (or any MCP client) to paint by sending commands to this plugin.
"""

from krita import *
from PyQt5.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QBrush, QLinearGradient, QRadialGradient
from PyQt5.QtCore import QTimer, QThread, pyqtSignal, QPointF, QRectF, QBuffer, QIODevice, Qt
from PyQt5.QtWidgets import QMessageBox
import json
import threading
import base64
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import os

# Configuration - customize these as needed
SERVER_PORT = 5678
CANVAS_OUTPUT_DIR = os.path.expanduser("~/krita-mcp-output")

class CommandQueue:
    """Thread-safe command queue for passing commands from HTTP thread to main thread."""
    def __init__(self):
        self.queue = []
        self.results = {}
        self.lock = threading.Lock()
        self.result_event = threading.Event()

    def push(self, command_id, command):
        with self.lock:
            self.queue.append((command_id, command))

    def pop(self):
        with self.lock:
            if self.queue:
                return self.queue.pop(0)
            return None

    def set_result(self, command_id, result):
        with self.lock:
            self.results[command_id] = result
        self.result_event.set()

    def get_result(self, command_id, timeout=120):
        """Wait for result with timeout."""
        for _ in range(int(timeout * 10)):  # Check every 100ms
            with self.lock:
                if command_id in self.results:
                    result = self.results.pop(command_id)
                    return result
            self.result_event.wait(0.1)
            self.result_event.clear()
        return {"error": "Timeout waiting for command execution"}

# Global command queue
command_queue = CommandQueue()
command_counter = 0

class PaintRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for paint commands."""

    def log_message(self, format, *args):
        pass

    def send_json_response(self, data, status=200):
        body = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))  # BUG FIX: was missing,
        # which forced the client to infer the response body's end from the
        # connection closing rather than a declared length -- unreliable for the
        # larger payloads that image preview/region/thumbnail responses produce.
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        """Handle GET requests - mainly for health check."""
        parsed = urlparse(self.path)

        if parsed.path == '/health':
            self.send_json_response({"status": "ok", "plugin": "kritamcp"})
        elif parsed.path == '/info':
            self.send_json_response({
                "status": "ok",
                "canvas_dir": CANVAS_OUTPUT_DIR,
                "commands": [
                    "new_canvas", "set_color", "set_brush", "stroke",
                    "fill", "draw_shape", "draw_path", "fill_gradient",
                    "get_canvas", "get_canvas_preview", "get_canvas_region",
                    "undo", "redo", "clear", "save", "get_color_at",
                    "list_brushes", "open_file", "list_layers",
                    "get_layer_thumbnail", "add_layer", "set_active_layer",
                    "delete_layer", "clear_layer"
                ]
            })
        else:
            self.send_json_response({"error": "Unknown endpoint"}, 404)

    def do_POST(self):
        """Handle POST requests - paint commands."""
        global command_counter

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')

        try:
            command = json.loads(body)
        except json.JSONDecodeError:
            self.send_json_response({"error": "Invalid JSON"}, 400)
            return

        command_counter += 1
        command_id = command_counter
        command_queue.push(command_id, command)

        result = command_queue.get_result(command_id)

        if "error" in result:
            self.send_json_response(result, 500)
        else:
            self.send_json_response(result)


class ServerThread(QThread):
    """Thread to run HTTP server without blocking Krita UI."""

    def __init__(self, port):
        super().__init__()
        self.port = port
        self.server = None

    def run(self):
        self.server = HTTPServer(('localhost', self.port), PaintRequestHandler)
        self.server.serve_forever()

    def stop(self):
        if self.server:
            self.server.shutdown()


class KritaMCPExtension(Extension):
    """Main Krita extension class."""

    def __init__(self, parent):
        super().__init__(parent)
        self.server_thread = None
        self.timer = None
        self.current_brush_size = 20
        self.current_opacity = 1.0

    def setup(self):
        pass

    def createActions(self, window):
        os.makedirs(CANVAS_OUTPUT_DIR, exist_ok=True)

        if self.server_thread is None:
            self.server_thread = ServerThread(SERVER_PORT)
            self.server_thread.start()
            print(f"[KritaMCP] HTTP server started on port {SERVER_PORT}")

        if self.timer is None:
            self.timer = QTimer()
            self.timer.timeout.connect(self.process_commands)
            self.timer.start(50)

    def process_commands(self):
        item = command_queue.pop()
        if item is None:
            return

        command_id, command = item
        result = self.execute_command(command)
        command_queue.set_result(command_id, result)

    def execute_command(self, command):
        try:
            action = command.get("action")
            params = command.get("params", {})

            if action == "new_canvas":
                return self.cmd_new_canvas(params)
            elif action == "set_color":
                return self.cmd_set_color(params)
            elif action == "set_brush":
                return self.cmd_set_brush(params)
            elif action == "stroke":
                return self.cmd_stroke(params)
            elif action == "fill":
                return self.cmd_fill(params)
            elif action == "draw_shape":
                return self.cmd_draw_shape(params)
            elif action == "get_canvas":
                return self.cmd_get_canvas(params)
            elif action == "undo":
                return self.cmd_undo(params)
            elif action == "redo":
                return self.cmd_redo(params)
            elif action == "clear":
                return self.cmd_clear(params)
            elif action == "save":
                return self.cmd_save(params)
            elif action == "get_color_at":
                return self.cmd_get_color_at(params)
            elif action == "list_brushes":
                return self.cmd_list_brushes(params)
            elif action == "open_file":
                return self.cmd_open_file(params)
            elif action == "get_canvas_preview":
                return self.cmd_get_canvas_preview(params)
            elif action == "get_canvas_region":
                return self.cmd_get_canvas_region(params)
            elif action == "list_layers":
                return self.cmd_list_layers(params)
            elif action == "get_layer_thumbnail":
                return self.cmd_get_layer_thumbnail(params)
            elif action == "draw_path":
                return self.cmd_draw_path(params)
            elif action == "fill_gradient":
                return self.cmd_fill_gradient(params)
            elif action == "add_layer":
                return self.cmd_add_layer(params)
            elif action == "set_active_layer":
                return self.cmd_set_active_layer(params)
            elif action == "delete_layer":
                return self.cmd_delete_layer(params)
            elif action == "clear_layer":
                return self.cmd_clear_layer(params)
            else:
                return {"error": f"Unknown action: {action}"}

        except Exception as e:
            return {"error": str(e)}

    def get_active_document(self):
        app = Krita.instance()
        return app.activeDocument()

    def get_active_view(self):
        app = Krita.instance()
        window = app.activeWindow()
        if window:
            return window.activeView()
        return None

    def get_active_layer(self):
        doc = self.get_active_document()
        if doc:
            return doc.activeNode()
        return None

    def _get_composition_mode(self, mode_str):
        modes = {
            "normal": QPainter.CompositionMode_SourceOver,
            "multiply": QPainter.CompositionMode_Multiply,
            "screen": QPainter.CompositionMode_Screen,
            "overlay": QPainter.CompositionMode_Overlay,
            "darken": QPainter.CompositionMode_Darken,
            "lighten": QPainter.CompositionMode_Lighten,
            "color dodge": QPainter.CompositionMode_ColorDodge,
            "color burn": QPainter.CompositionMode_ColorBurn,
            "hard light": QPainter.CompositionMode_HardLight,
            "soft light": QPainter.CompositionMode_SoftLight,
            "difference": QPainter.CompositionMode_Difference,
            "exclusion": QPainter.CompositionMode_Exclusion,
            "clear": QPainter.CompositionMode_Clear
        }
        return modes.get(mode_str.lower(), QPainter.CompositionMode_SourceOver)

    def _apply_qpainter(self, doc, layer, draw_func):
        w, h = doc.width(), doc.height()
        pixel_data = layer.pixelData(0, 0, w, h)
        image = QImage(pixel_data, w, h, QImage.Format_ARGB32)
        
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        draw_func(painter)
        painter.end()
        
        ptr = image.bits()
        ptr.setsize(image.byteCount())
        layer.setPixelData(bytes(ptr), 0, 0, w, h)
        doc.refreshProjection()

    def cmd_draw_path(self, params):
        doc = self.get_active_document()
        layer = self.get_active_layer()
        if not doc or not layer: 
            return {"error": "No active document or layer"}

        points = params.get("points", [])
        if not points: 
            return {"error": "No points provided"}

        is_bezier = params.get("is_bezier", False)
        size = params.get("size", 5.0)
        color = QColor(params.get("color", "#ffffff"))
        opacity = params.get("opacity", 1.0)
        blend_mode = params.get("blend_mode", "normal")

        def draw(painter):
            painter.setCompositionMode(self._get_composition_mode(blend_mode))
            painter.setOpacity(opacity)
            
            pen = QPen(color, size, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            
            path = QPainterPath()
            path.moveTo(points[0][0], points[0][1])
            
            if is_bezier:
                idx = 1
                while idx + 2 < len(points):
                    path.cubicTo(
                        points[idx][0], points[idx][1],
                        points[idx+1][0], points[idx+1][1],
                        points[idx+2][0], points[idx+2][1]
                    )
                    idx += 3
            else:
                for pt in points[1:]:
                    path.lineTo(pt[0], pt[1])
                    
            painter.drawPath(path)

        self._apply_qpainter(doc, layer, draw)
        return {"status": "ok"}

    def cmd_fill_gradient(self, params):
        doc = self.get_active_document()
        layer = self.get_active_layer()
        if not doc or not layer: 
            return {"error": "No active document or layer"}

        gtype = params.get("type", "linear")
        x1, y1 = params.get("x1", 0), params.get("y1", 0)
        x2, y2 = params.get("x2", doc.width()), params.get("y2", doc.height())
        color_stops = params.get("color_stops", [])
        opacity = params.get("opacity", 1.0)
        blend_mode = params.get("blend_mode", "normal")

        def draw(painter):
            painter.setCompositionMode(self._get_composition_mode(blend_mode))
            painter.setOpacity(opacity)
            
            if gtype == "radial":
                radius = ((x2 - x1)**2 + (y2 - y1)**2)**0.5
                gradient = QRadialGradient(x1, y1, radius)
            else:
                gradient = QLinearGradient(x1, y1, x2, y2)
                
            for stop in color_stops:
                gradient.setColorAt(stop["position"], QColor(stop["color"]))
                
            painter.fillRect(0, 0, doc.width(), doc.height(), QBrush(gradient))

        self._apply_qpainter(doc, layer, draw)
        return {"status": "ok"}
    
    def cmd_new_canvas(self, params):
        width = params.get("width", 800)
        height = params.get("height", 600)
        name = params.get("name", "New Canvas")
        bg_color = params.get("background", "#1a1a2e")

        app = Krita.instance()
        doc = app.createDocument(width, height, name, "RGBA", "U8", "", 120.0)

        window = app.activeWindow()
        if window:
            window.addView(doc)

        root = doc.rootNode()

        # createDocument() auto-creates its own default layer (e.g. "Background").
        # Remove it so the canvas starts with exactly one known layer instead of a
        # hidden extra one sitting in the stack alongside ours.
        for existing in list(root.childNodes()):
            existing.remove()

        layer = doc.createNode("paint", "paintlayer")
        root.addChildNode(layer, None)
        doc.setActiveNode(layer)  # BUG FIX: without this, doc.activeNode() stayed on
        # Krita's auto-created default layer, so every subsequent stroke/fill/draw_shape
        # call (which paints onto get_active_layer()) could silently target the wrong
        # layer instead of the one that's actually visible/intended.

        color = QColor(bg_color)
        r, g, b = color.red(), color.green(), color.blue()

        pixel_data = bytes([b, g, r, 255] * (width * height))
        layer.setPixelData(pixel_data, 0, 0, width, height)

        doc.refreshProjection()

        return {"status": "ok", "width": width, "height": height, "name": name}

    def cmd_set_color(self, params):
        color_hex = params.get("color", "#ffffff")

        view = self.get_active_view()
        if not view:
            return {"error": "No active view"}

        color = QColor(color_hex)
        mc = ManagedColor.fromQColor(color, view.canvas())
        view.setForeGroundColor(mc)

        return {"status": "ok", "color": color_hex}

    def cmd_set_brush(self, params):
        preset_name = params.get("preset", None)
        size = params.get("size", None)
        opacity = params.get("opacity", None)

        view = self.get_active_view()
        if not view:
            return {"error": "No active view"}

        if preset_name:
            presets = Krita.instance().resources("preset")
            found = None
            for name, preset in presets.items():
                if preset_name.lower() in name.lower():
                    found = preset
                    break
            if found:
                view.setCurrentBrushPreset(found)
            else:
                return {"error": f"Brush preset not found: {preset_name}"}

        if size is not None:
            self.current_brush_size = size
            view.setBrushSize(size)

        if opacity is not None:
            self.current_opacity = opacity

        return {"status": "ok", "preset": preset_name, "size": size, "opacity": opacity}

    def cmd_stroke(self, params):
        points = params.get("points", [])
        brush_size = params.get("size", self.current_brush_size)
        hardness = params.get("hardness", 0.5)
        pressure = params.get("pressure", 1.0)
        base_opacity = params.get("opacity", self.current_opacity)
        opacity = max(0.0, min(1.0, base_opacity * pressure))

        if len(points) < 2:
            return {"error": "Need at least 2 points for a stroke"}

        layer = self.get_active_layer()
        if not layer:
            return {"error": "No active layer"}

        doc = self.get_active_document()
        view = self.get_active_view()

        if not view:
            return {"error": "No active view"}

        fg = view.foregroundColor()
        qcolor = fg.colorForCanvas(view.canvas())
        r, g, b = qcolor.red(), qcolor.green(), qcolor.blue()

        width = doc.width()
        height = doc.height()
        radius = max(1, brush_size // 2)

        min_x = max(0, int(min(p[0] for p in points)) - radius - 2)
        min_y = max(0, int(min(p[1] for p in points)) - radius - 2)
        max_x = min(width, int(max(p[0] for p in points)) + radius + 2)
        max_y = min(height, int(max(p[1] for p in points)) + radius + 2)

        w = max_x - min_x
        h = max_y - min_y

        if w <= 0 or h <= 0:
            return {"error": "Stroke out of bounds"}

        existing = layer.pixelData(min_x, min_y, w, h)
        pixels = bytearray(existing)

        import math

        def draw_soft_circle(cx, cy, point_opacity=1.0):
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    dist_sq = dx*dx + dy*dy
                    if dist_sq <= radius*radius:
                        px = int(cx) + dx - min_x
                        py = int(cy) + dy - min_y
                        if 0 <= px < w and 0 <= py < h:
                            dist = math.sqrt(dist_sq) / radius if radius > 0 else 0

                            if hardness >= 1.0:
                                alpha_factor = 1.0
                            else:
                                if dist < hardness:
                                    alpha_factor = 1.0
                                else:
                                    falloff = (dist - hardness) / (1.0 - hardness) if hardness < 1.0 else 0
                                    alpha_factor = 1.0 - falloff

                            final_alpha = int(255 * alpha_factor * opacity * point_opacity)

                            if final_alpha > 0:
                                idx = (py * w + px) * 4
                                existing_b = pixels[idx]
                                existing_g = pixels[idx+1]
                                existing_r = pixels[idx+2]
                                existing_a = pixels[idx+3]

                                blend = final_alpha / 255.0
                                new_r = int(existing_r * (1 - blend) + r * blend)
                                new_g = int(existing_g * (1 - blend) + g * blend)
                                new_b = int(existing_b * (1 - blend) + b * blend)
                                new_a = max(existing_a, final_alpha)

                                pixels[idx] = new_b
                                pixels[idx+1] = new_g
                                pixels[idx+2] = new_r
                                pixels[idx+3] = new_a

        def draw_line(x1, y1, x2, y2):
            dist = math.sqrt((x2 - x1)**2 + (y2 - y1)**2)
            steps = max(1, int(dist / max(1, radius / 3)))

            for i in range(steps + 1):
                t = i / steps if steps > 0 else 0
                x = x1 + t * (x2 - x1)
                y = y1 + t * (y2 - y1)
                draw_soft_circle(x, y)

        for i in range(len(points)):
            draw_soft_circle(points[i][0], points[i][1])
            if i > 0:
                draw_line(points[i-1][0], points[i-1][1], points[i][0], points[i][1])

        layer.setPixelData(bytes(pixels), min_x, min_y, w, h)
        doc.refreshProjection()

        return {"status": "ok", "points_count": len(points), "hardness": hardness}

    def cmd_fill(self, params):
        x = params.get("x", 0)
        y = params.get("y", 0)
        radius = params.get("radius", 50)

        layer = self.get_active_layer()
        if not layer:
            return {"error": "No active layer"}

        doc = self.get_active_document()
        view = self.get_active_view()

        if not view:
            return {"error": "No active view"}

        fg = view.foregroundColor()
        qcolor = fg.colorForCanvas(view.canvas())
        r, g, b = qcolor.red(), qcolor.green(), qcolor.blue()

        x1 = max(0, x - radius)
        y1 = max(0, y - radius)
        x2 = min(doc.width(), x + radius)
        y2 = min(doc.height(), y + radius)
        w = x2 - x1
        h = y2 - y1

        if w <= 0 or h <= 0:
            return {"error": "Fill area out of bounds"}

        existing = layer.pixelData(x1, y1, w, h)
        pixels = bytearray(existing)

        for py in range(h):
            for px in range(w):
                dx = (x1 + px) - x
                dy = (y1 + py) - y
                if dx*dx + dy*dy <= radius*radius:
                    idx = (py * w + px) * 4
                    pixels[idx] = b
                    pixels[idx+1] = g
                    pixels[idx+2] = r
                    pixels[idx+3] = 255

        layer.setPixelData(bytes(pixels), x1, y1, w, h)
        doc.refreshProjection()

        return {"status": "ok", "x": x, "y": y, "radius": radius}

    def cmd_draw_shape(self, params):
        shape = params.get("shape", "rectangle")
        x = params.get("x", 0)
        y = params.get("y", 0)
        width = params.get("width", 100)
        height = params.get("height", 100)
        fill = params.get("fill", True)

        layer = self.get_active_layer()
        if not layer:
            return {"error": "No active layer"}

        doc = self.get_active_document()
        view = self.get_active_view()

        if not view:
            return {"error": "No active view"}

        fg = view.foregroundColor()
        qcolor = fg.colorForCanvas(view.canvas())
        r, g, b = qcolor.red(), qcolor.green(), qcolor.blue()

        if shape == "line":
            x2 = params.get("x2", x + width)
            y2 = params.get("y2", y + height)
            line_width = params.get("line_width", 2)

            x1_bound = max(0, int(min(x, x2)) - line_width)
            y1_bound = max(0, int(min(y, y2)) - line_width)
            x2_bound = min(doc.width(), int(max(x, x2)) + line_width)
            y2_bound = min(doc.height(), int(max(y, y2)) + line_width)
            w = x2_bound - x1_bound
            h = y2_bound - y1_bound

            if w > 0 and h > 0:
                existing = layer.pixelData(x1_bound, y1_bound, w, h)
                pixels = bytearray(existing)

                dist = max(abs(x2 - x), abs(y2 - y))
                steps = max(1, int(dist))
                radius = max(1, line_width // 2)

                for i in range(steps + 1):
                    t = i / steps if steps > 0 else 0
                    cx = x + t * (x2 - x)
                    cy = y + t * (y2 - y)
                    for dy in range(-radius, radius + 1):
                        for dx in range(-radius, radius + 1):
                            if dx*dx + dy*dy <= radius*radius:
                                px = int(cx) + dx - x1_bound
                                py = int(cy) + dy - y1_bound
                                if 0 <= px < w and 0 <= py < h:
                                    idx = (py * w + px) * 4
                                    pixels[idx] = b
                                    pixels[idx+1] = g
                                    pixels[idx+2] = r
                                    pixels[idx+3] = 255

                layer.setPixelData(bytes(pixels), x1_bound, y1_bound, w, h)
        elif shape in ("rectangle", "ellipse"):
            stroke = params.get("stroke", False)  # BUG FIX: this was accepted by
            # server.py's tool schema but never actually read here, so outline-only
            # shapes (fill=False, stroke=True) always fell through to the
            # "not supported" error below, no matter what the caller asked for.

            if not fill and not stroke:
                return {"error": f"Shape '{shape}' needs fill=True and/or stroke=True"}

            line_width = params.get("line_width", 2)

            def draw(painter):
                painter.setBrush(QBrush(qcolor) if fill else Qt.NoBrush)
                painter.setPen(QPen(qcolor, line_width) if stroke else Qt.NoPen)
                rect = QRectF(x, y, width, height)
                if shape == "rectangle":
                    painter.drawRect(rect)
                else:
                    painter.drawEllipse(rect)

            self._apply_qpainter(doc, layer, draw)
        else:
            return {"error": f"Shape '{shape}' with current options not supported"}

        doc.refreshProjection()

        return {"status": "ok", "shape": shape}

    def cmd_get_canvas(self, params):
        filename = params.get("filename", "canvas.png")

        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}

        if not filename.endswith('.png'):
            filename += '.png'

        filepath = os.path.join(CANVAS_OUTPUT_DIR, filename)

        doc.setBatchmode(True)
        doc.exportImage(filepath, InfoObject())
        doc.setBatchmode(False)

        return {"status": "ok", "path": filepath}

    def _qimage_to_base64(self, image):
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        png_bytes = bytes(buffer.data())          # copy into Python bytes immediately
        return base64.b64encode(png_bytes).decode('utf-8')  # use Python's base64, not Qt's

    def cmd_get_canvas_preview(self, params):
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}

        max_dim = params.get("max_dimension", 0)
        w = doc.width()
        h = doc.height()

        pixel_data = bytes(doc.rootNode().projectionPixelData(0, 0, w, h))  # bytes() keeps buffer alive
        image = QImage(pixel_data, w, h, QImage.Format_ARGB32)

        if max_dim > 0 and (w > max_dim or h > max_dim):
            image = image.scaled(max_dim, max_dim, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        b64 = self._qimage_to_base64(image)
        return {"status": "ok", "base64": b64, "width": image.width(), "height": image.height()}

    def cmd_get_canvas_region(self, params):
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}

        x = max(0, int(params.get("x", 0)))
        y = max(0, int(params.get("y", 0)))
        w = min(doc.width() - x, int(params.get("width", 100)))
        h = min(doc.height() - y, int(params.get("height", 100)))

        if w <= 0 or h <= 0:
            return {"error": "Invalid region bounds"}

        pixel_data = bytes(doc.rootNode().projectionPixelData(x, y, w, h))  # bytes() keeps buffer alive
        image = QImage(pixel_data, w, h, QImage.Format_ARGB32)

        b64 = self._qimage_to_base64(image)
        return {"status": "ok", "base64": b64, "width": w, "height": h, "x": x, "y": y}

    def _find_node(self, parent, target):
        """Depth-first search for a node by exact name."""
        for node in parent.childNodes():
            if node.name() == target:
                return node
            found = self._find_node(node, target)
            if found:
                return found
        return None

    def cmd_list_layers(self, params):
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}

        layers = doc.rootNode().childNodes()
        result = []

        for node in layers:
            result.append({
                "name": node.name(),
                "visible": node.visible(),
                "opacity": node.opacity(),
                "type": node.type(),
            })

        return {"status": "ok", "layers": result}

    def cmd_get_layer_thumbnail(self, params):
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}

        name = params.get("name")
        if not name:
            return {"error": "No layer name specified"}

        node = self._find_node(doc.rootNode(), name)
        if not node:
            return {"error": f"Layer not found: {name}"}

        size = max(16, min(512, int(params.get("size", 128))))
        thumb = node.thumbnail(size, size)

        # BUG FIX: `if thumb else None` never catches a null QImage -- Qt objects
        # are truthy in Python regardless of isNull(). Check isNull() explicitly.
        if thumb is None or thumb.isNull():
            return {"error": f"Could not generate a thumbnail for layer: {name}"}

        b64 = self._qimage_to_base64(thumb)
        return {"status": "ok", "base64": b64, "width": thumb.width(), "height": thumb.height()}

    def cmd_add_layer(self, params):
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}

        name = params.get("name", "Layer")
        new_layer = doc.createNode(name, "paintlayer")

        active = doc.activeNode()
        parent = active.parentNode() if active else doc.rootNode()
        parent.addChildNode(new_layer, active)  # inserts above active node

        doc.setActiveNode(new_layer)
        doc.refreshProjection()

        return {"status": "ok", "name": name}

    def cmd_set_active_layer(self, params):
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}

        name = params.get("name")
        if not name:
            return {"error": "No layer name specified"}

        node = self._find_node(doc.rootNode(), name)
        if not node:
            return {"error": f"Layer not found: {name}"}

        doc.setActiveNode(node)
        return {"status": "ok", "name": name}

    def cmd_delete_layer(self, params):
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}

        name = params.get("name")
        if not name:
            return {"error": "No layer name specified"}

        node = self._find_node(doc.rootNode(), name)
        if not node:
            return {"error": f"Layer not found: {name}"}

        node.remove()
        doc.refreshProjection()
        return {"status": "ok", "deleted": name}

    def cmd_clear_layer(self, params):
        """Clear the active layer to fully transparent."""
        doc = self.get_active_document()
        layer = self.get_active_layer()
        if not doc or not layer:
            return {"error": "No active document or layer"}

        w, h = doc.width(), doc.height()
        transparent = bytes([0, 0, 0, 0] * (w * h))
        layer.setPixelData(transparent, 0, 0, w, h)
        doc.refreshProjection()

        return {"status": "ok", "layer": layer.name()}
    
    def cmd_undo(self, params):
        app = Krita.instance()
        action = app.action('edit_undo')
        if action:
            action.trigger()
            return {"status": "ok"}
        return {"error": "Could not trigger undo"}

    def cmd_redo(self, params):
        app = Krita.instance()
        action = app.action('edit_redo')
        if action:
            action.trigger()
            return {"status": "ok"}
        return {"error": "Could not trigger redo"}

    def cmd_clear(self, params):
        layer = self.get_active_layer()
        if not layer:
            return {"error": "No active layer"}

        doc = self.get_active_document()

        width = doc.width()
        height = doc.height()

        bg_color = params.get("color", "#1a1a2e")
        color = QColor(bg_color)
        r, g, b = color.red(), color.green(), color.blue()

        pixel_data = bytes([b, g, r, 255] * (width * height))
        layer.setPixelData(pixel_data, 0, 0, width, height)

        doc.refreshProjection()

        return {"status": "ok", "color": bg_color}

    def cmd_save(self, params):
        filepath = params.get("path")
        if not filepath:
            return {"error": "No path specified"}

        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}

        doc.setBatchmode(True)
        doc.exportImage(filepath, InfoObject())
        doc.setBatchmode(False)

        return {"status": "ok", "path": filepath}

    def cmd_get_color_at(self, params):
        x = params.get("x", 0)
        y = params.get("y", 0)

        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}

        layer = doc.rootNode()
        pixel_data = layer.projectionPixelData(x, y, 1, 1)

        if len(pixel_data) >= 4:
            b, g, r, a = pixel_data[0], pixel_data[1], pixel_data[2], pixel_data[3]
            hex_color = "#{:02x}{:02x}{:02x}".format(r, g, b)
            return {"status": "ok", "color": hex_color, "r": r, "g": g, "b": b, "a": a}

        return {"error": "Could not read pixel"}

    def cmd_list_brushes(self, params):
        filter_str = params.get("filter", "")
        limit = params.get("limit", 50)

        presets = Krita.instance().resources("preset")
        brush_list = []

        for name, preset in presets.items():
            if filter_str.lower() in name.lower():
                brush_list.append(name)
                if len(brush_list) >= limit:
                    break

        return {"status": "ok", "brushes": brush_list, "count": len(brush_list)}

    def cmd_open_file(self, params):
        filepath = params.get("path")
        if not filepath:
            return {"error": "No path specified"}

        if not os.path.exists(filepath):
            return {"error": f"File not found: {filepath}"}

        app = Krita.instance()

        doc = app.openDocument(filepath)
        if not doc:
            return {"error": f"Failed to open: {filepath}"}

        window = app.activeWindow()
        if window:
            window.addView(doc)

        return {"status": "ok", "path": filepath, "name": doc.name(), "width": doc.width(), "height": doc.height()}


Krita.instance().addExtension(KritaMCPExtension(Krita.instance()))