from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Redirect(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(302)
        self.send_header('Location', 'http://127.0.0.1:8090/')
        self.end_headers()

ThreadingHTTPServer(('127.0.0.1', 3009), Redirect).serve_forever()
