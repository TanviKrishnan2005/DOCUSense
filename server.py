import cgi
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from docx import Document
from pypdf import PdfReader


BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "web"
UPLOADS_DIR = BASE_DIR / "uploads"
DB_PATH = BASE_DIR / "docu_sense.db"
PORT = int(os.environ.get("PORT", "8035"))
HOST = os.environ.get("HOST", "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")

UPLOADS_DIR.mkdir(exist_ok=True)

SESSIONS = {}
STOP_WORDS = {
    "the", "is", "am", "are", "a", "an", "to", "of", "in", "on", "for", "and",
    "or", "with", "this", "that", "from", "by", "it", "as", "be", "was", "were",
    "at", "can", "you", "your", "what", "which", "how", "when", "where", "who",
    "do", "does", "did", "about", "into", "their", "them", "they", "we", "our",
}


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def setup_db():
    conn = connect_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            file_name TEXT NOT NULL,
            file_type TEXT NOT NULL,
            storage_path TEXT NOT NULL,
            content_preview TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            source_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def hash_password(password):
    import hashlib
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def load_json_body(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length).decode("utf-8")
    return json.loads(raw or "{}")


def clean_text(text):
    text = text.replace("\x00", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def split_into_chunks(text, size=520, overlap=90):
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def tokenize(text):
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return [word for word in words if word not in STOP_WORDS and len(word) > 1]


def vectorize(tokens):
    counts = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    return counts


def cosine_score(vec_a, vec_b):
    if not vec_a or not vec_b:
        return 0
    dot = 0
    norm_a = 0
    norm_b = 0
    for key, value in vec_a.items():
        norm_a += value * value
        dot += value * vec_b.get(key, 0)
    for value in vec_b.values():
        norm_b += value * value
    if norm_a == 0 or norm_b == 0:
        return 0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def read_pdf(file_path):
    reader = PdfReader(str(file_path))
    pieces = []
    for page in reader.pages:
        pieces.append(page.extract_text() or "")
    return clean_text(" ".join(pieces))


def read_docx(file_path):
    doc = Document(str(file_path))
    pieces = [paragraph.text for paragraph in doc.paragraphs]
    return clean_text(" ".join(pieces))


def read_txt(file_path):
    return clean_text(file_path.read_text(encoding="utf-8", errors="ignore"))


def read_document(file_path, extension):
    if extension == ".pdf":
        return read_pdf(file_path)
    if extension == ".docx":
        return read_docx(file_path)
    if extension == ".txt":
        return read_txt(file_path)
    raise ValueError("Unsupported file type")


def make_answer(question, results):
    if not results:
        return "I could not find a clear answer in the uploaded documents."

    question_words = set(tokenize(question))
    snippets = []
    for item in results[:3]:
        text = item["content"]
        sentences = re.split(r"(?<=[.!?])\s+", text)
        picked = []
        for sentence in sentences:
            sentence_words = set(tokenize(sentence))
            if question_words & sentence_words:
                picked.append(sentence.strip())
            if len(picked) == 2:
                break
        if not picked:
            picked.append(text[:180].strip())
        snippets.extend(picked)

    joined = " ".join(snippets)
    joined = re.sub(r"\s+", " ", joined).strip()
    if len(joined) > 420:
        joined = joined[:417].rstrip() + "..."
    return joined or "I found related content, but it was too weak to form a useful answer."


def get_user_from_token(handler):
    auth = handler.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth.split(" ", 1)[1].strip()
    user_id = SESSIONS.get(token)
    if not user_id:
        return None
    conn = connect_db()
    row = conn.execute("SELECT id, name, email FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


class AppHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.add_common_headers("application/json")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.handle_api_get(parsed)
            return
        self.serve_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/signup":
            self.handle_signup()
        elif parsed.path == "/api/login":
            self.handle_login()
        elif parsed.path == "/api/upload":
            self.handle_upload()
        elif parsed.path == "/api/ask":
            self.handle_ask()
        else:
            self.send_json({"error": "Route not found"}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/documents/"):
            self.handle_delete_document(parsed.path)
        else:
            self.send_json({"error": "Route not found"}, 404)

    def log_message(self, format_text, *args):
        return

    def add_common_headers(self, content_type):
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.add_common_headers("application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, path):
        safe_path = path.lstrip("/") or "index.html"
        target = FRONTEND_DIR / safe_path
        if path == "/" or not target.exists() or target.is_dir():
            target = FRONTEND_DIR / "index.html"

        if not target.exists():
            self.send_error(404)
            return

        ext = target.suffix.lower()
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".svg": "image/svg+xml",
        }.get(ext, "application/octet-stream")

        data = target.read_bytes()
        self.send_response(200)
        self.add_common_headers(content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_api_get(self, parsed):
        user = get_user_from_token(self)
        if parsed.path == "/api/me":
            if not user:
                self.send_json({"error": "Unauthorized"}, 401)
                return
            self.send_json({"user": user})
            return

        if not user:
            self.send_json({"error": "Unauthorized"}, 401)
            return

        if parsed.path == "/api/documents":
            conn = connect_db()
            rows = conn.execute(
                "SELECT id, title, file_name, file_type, content_preview, created_at FROM documents WHERE user_id = ? ORDER BY id DESC",
                (user["id"],),
            ).fetchall()
            conn.close()
            self.send_json({"documents": [dict(row) for row in rows]})
            return

        if parsed.path == "/api/history":
            conn = connect_db()
            rows = conn.execute(
                "SELECT id, question, answer, source_json, created_at FROM chats WHERE user_id = ? ORDER BY id DESC LIMIT 12",
                (user["id"],),
            ).fetchall()
            conn.close()
            history = []
            for row in rows:
                item = dict(row)
                item["sources"] = json.loads(item.pop("source_json"))
                history.append(item)
            self.send_json({"history": history})
            return

        self.send_json({"error": "Route not found"}, 404)

    def handle_signup(self):
        data = load_json_body(self)
        name = clean_text(data.get("name", ""))
        email = clean_text(data.get("email", "")).lower()
        password = data.get("password", "").strip()

        if len(name) < 2 or "@" not in email or len(password) < 4:
            self.send_json({"error": "Please enter valid signup details."}, 400)
            return

        conn = connect_db()
        try:
            cur = conn.execute(
                "INSERT INTO users (name, email, password, created_at) VALUES (?, ?, ?, ?)",
                (name, email, hash_password(password), now_text()),
            )
            conn.commit()
            user_id = cur.lastrowid
        except sqlite3.IntegrityError:
            conn.close()
            self.send_json({"error": "An account already exists with this email."}, 400)
            return
        conn.close()

        token = secrets.token_hex(16)
        SESSIONS[token] = user_id
        self.send_json({"token": token, "user": {"id": user_id, "name": name, "email": email}}, 201)

    def handle_login(self):
        data = load_json_body(self)
        email = clean_text(data.get("email", "")).lower()
        password = data.get("password", "").strip()

        conn = connect_db()
        row = conn.execute(
            "SELECT id, name, email, password FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        conn.close()

        if not row or row["password"] != hash_password(password):
            self.send_json({"error": "Invalid email or password."}, 401)
            return

        token = secrets.token_hex(16)
        SESSIONS[token] = row["id"]
        self.send_json({"token": token, "user": {"id": row["id"], "name": row["name"], "email": row["email"]}})

    def handle_upload(self):
        user = get_user_from_token(self)
        if not user:
            self.send_json({"error": "Unauthorized"}, 401)
            return

        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type"),
            },
        )
        uploaded = form["file"] if "file" in form else None
        if uploaded is None or not getattr(uploaded, "filename", ""):
            self.send_json({"error": "Please choose a file."}, 400)
            return

        original_name = Path(uploaded.filename).name
        extension = Path(original_name).suffix.lower()
        if extension not in {".pdf", ".docx", ".txt"}:
            self.send_json({"error": "Only PDF, DOCX, and TXT files are allowed."}, 400)
            return

        safe_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}{extension}"
        saved_path = UPLOADS_DIR / safe_name
        with open(saved_path, "wb") as target:
            shutil.copyfileobj(uploaded.file, target)

        try:
            text = read_document(saved_path, extension)
        except Exception:
            saved_path.unlink(missing_ok=True)
            self.send_json({"error": "The file could not be read."}, 400)
            return

        if len(text) < 40:
            saved_path.unlink(missing_ok=True)
            self.send_json({"error": "The file is too short or empty."}, 400)
            return

        chunks = split_into_chunks(text)
        preview = text[:180]

        conn = connect_db()
        cur = conn.execute(
            """
            INSERT INTO documents (user_id, title, file_name, file_type, storage_path, content_preview, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user["id"], Path(original_name).stem, original_name, extension.replace(".", "").upper(), str(saved_path), preview, now_text()),
        )
        document_id = cur.lastrowid
        for index, chunk in enumerate(chunks):
            conn.execute(
                "INSERT INTO chunks (document_id, chunk_index, content) VALUES (?, ?, ?)",
                (document_id, index, chunk),
            )
        conn.commit()
        row = conn.execute(
            "SELECT id, title, file_name, file_type, content_preview, created_at FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        conn.close()
        self.send_json({"document": dict(row)}, 201)

    def handle_delete_document(self, path):
        user = get_user_from_token(self)
        if not user:
            self.send_json({"error": "Unauthorized"}, 401)
            return

        try:
            document_id = int(path.rsplit("/", 1)[1])
        except ValueError:
            self.send_json({"error": "Invalid document id."}, 400)
            return

        conn = connect_db()
        row = conn.execute(
            "SELECT id, storage_path FROM documents WHERE id = ? AND user_id = ?",
            (document_id, user["id"]),
        ).fetchone()
        if not row:
            conn.close()
            self.send_json({"error": "Document not found."}, 404)
            return

        conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        conn.commit()
        conn.close()
        Path(row["storage_path"]).unlink(missing_ok=True)
        self.send_json({"message": "Document deleted."})

    def handle_ask(self):
        user = get_user_from_token(self)
        if not user:
            self.send_json({"error": "Unauthorized"}, 401)
            return

        data = load_json_body(self)
        question = clean_text(data.get("question", ""))
        document_filter = data.get("documentId")

        if len(question) < 4:
            self.send_json({"error": "Please type a proper question."}, 400)
            return

        conn = connect_db()
        params = [user["id"]]
        query = """
            SELECT c.content, d.title, d.file_name
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE d.user_id = ?
        """
        if document_filter:
            query += " AND d.id = ?"
            params.append(document_filter)
        rows = conn.execute(query, params).fetchall()

        if not rows:
            conn.close()
            self.send_json({"error": "Please upload a document first."}, 400)
            return

        question_vector = vectorize(tokenize(question))
        ranked = []
        for row in rows:
            chunk_text = row["content"]
            score = cosine_score(question_vector, vectorize(tokenize(chunk_text)))
            if score > 0:
                ranked.append(
                    {
                        "score": score,
                        "content": chunk_text,
                        "title": row["title"],
                        "file_name": row["file_name"],
                    }
                )
        ranked.sort(key=lambda item: item["score"], reverse=True)
        top_results = ranked[:3]
        answer = make_answer(question, top_results)
        sources = [
            {
                "title": item["title"],
                "fileName": item["file_name"],
                "snippet": item["content"][:220].strip(),
                "score": round(item["score"], 3),
            }
            for item in top_results
        ]

        conn.execute(
            "INSERT INTO chats (user_id, question, answer, source_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (user["id"], question, answer, json.dumps(sources), now_text()),
        )
        conn.commit()
        conn.close()
        self.send_json({"answer": answer, "sources": sources})


if __name__ == "__main__":
    setup_db()
    print(f"DocuSense running at http://{HOST}:{PORT}")
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
