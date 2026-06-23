# DocuSense

DocuSense is a simple document question-answering app built for student-level portfolio use.

Users can:
- create an account
- upload PDF, DOCX, and TXT files
- ask questions from uploaded files
- see source snippets used for the answer
- view recent chat history

## Tech Used

- Frontend: HTML, CSS, JavaScript
- Backend: Python
- Database: SQLite

## How it works

1. Upload a document
2. The app reads the content
3. The content is split into small chunks
4. The app finds the most relevant chunks for the question
5. It returns a short answer and shows source snippets

## Run

Use the bundled Python runtime:

```powershell
& "C:\Users\r.sethuramalingom\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" server.py
```

Then open:

`http://127.0.0.1:8035`

## Notes

- The app supports PDF, DOCX, and TXT files
- Data is stored locally in SQLite
- If no good match is found, the app says so clearly
