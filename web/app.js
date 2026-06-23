const state = {
  token: localStorage.getItem("docuSenseToken") || "",
  user: null,
  documents: [],
};

const authView = document.getElementById("authView");
const appView = document.getElementById("appView");
const welcomeText = document.getElementById("welcomeText");
const documentList = document.getElementById("documentList");
const documentFilter = document.getElementById("documentFilter");
const uploadMessage = document.getElementById("uploadMessage");
const answerBox = document.getElementById("answerBox");
const sourceList = document.getElementById("sourceList");
const historyList = document.getElementById("historyList");
const toast = document.getElementById("toast");

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2200);
}

async function api(path, options = {}) {
  const config = { ...options };
  config.headers = config.headers || {};
  if (state.token) {
    config.headers.Authorization = `Bearer ${state.token}`;
  }
  const response = await fetch(path, config);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || "Something went wrong");
  }
  return data;
}

function switchView(isLoggedIn) {
  authView.classList.toggle("active", !isLoggedIn);
  appView.classList.toggle("active", isLoggedIn);
}

function saveSession(token, user) {
  state.token = token;
  state.user = user;
  localStorage.setItem("docuSenseToken", token);
  welcomeText.textContent = `Welcome, ${user.name}`;
  switchView(true);
}

function clearSession() {
  state.token = "";
  state.user = null;
  state.documents = [];
  localStorage.removeItem("docuSenseToken");
  switchView(false);
}

function renderDocuments() {
  documentList.innerHTML = "";
  documentFilter.innerHTML = `<option value="">Search all documents</option>`;

  if (!state.documents.length) {
    documentList.innerHTML = `<div class="empty-state">No documents uploaded yet.</div>`;
    return;
  }

  state.documents.forEach((doc) => {
    const item = document.createElement("div");
    item.className = "doc-item";
    item.innerHTML = `
      <div class="doc-head">
        <div>
          <p class="doc-title">${doc.title}</p>
          <p class="doc-meta">${doc.file_name} • ${doc.file_type} • ${doc.created_at}</p>
        </div>
        <button class="mini-btn" data-id="${doc.id}">Delete</button>
      </div>
      <p class="source-snippet">${doc.content_preview}</p>
    `;
    item.querySelector("button").addEventListener("click", () => deleteDocument(doc.id));
    documentList.appendChild(item);

    const option = document.createElement("option");
    option.value = doc.id;
    option.textContent = doc.title;
    documentFilter.appendChild(option);
  });
}

function renderSources(sources) {
  sourceList.innerHTML = "";
  if (!sources.length) {
    return;
  }
  sources.forEach((source) => {
    const item = document.createElement("div");
    item.className = "source-item";
    item.innerHTML = `
      <p class="source-title">${source.title}</p>
      <p class="doc-meta">${source.fileName} • score ${source.score}</p>
      <p class="source-snippet">${source.snippet}</p>
    `;
    sourceList.appendChild(item);
  });
}

function renderHistory(history) {
  historyList.innerHTML = "";
  if (!history.length) {
    historyList.innerHTML = `<div class="empty-state">No questions asked yet.</div>`;
    return;
  }
  history.forEach((item) => {
    const card = document.createElement("div");
    card.className = "history-item";
    card.innerHTML = `
      <p class="history-question">${item.question}</p>
      <p class="doc-meta">${item.created_at}</p>
      <p class="history-answer">${item.answer}</p>
    `;
    historyList.appendChild(card);
  });
}

async function loadDocuments() {
  const data = await api("/api/documents");
  state.documents = data.documents;
  renderDocuments();
}

async function loadHistory() {
  const data = await api("/api/history");
  renderHistory(data.history);
}

async function loadProfile() {
  const data = await api("/api/me");
  state.user = data.user;
  welcomeText.textContent = `Welcome, ${data.user.name}`;
  switchView(true);
  await loadDocuments();
  await loadHistory();
}

async function deleteDocument(id) {
  try {
    await api(`/api/documents/${id}`, { method: "DELETE" });
    await loadDocuments();
    showToast("Document deleted");
  } catch (error) {
    showToast(error.message);
  }
}

document.getElementById("signupForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(event.target);
  try {
    const data = await api("/api/signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: formData.get("name"),
        email: formData.get("email"),
        password: formData.get("password"),
      }),
    });
    saveSession(data.token, data.user);
    await loadDocuments();
    await loadHistory();
    event.target.reset();
    showToast("Account created");
  } catch (error) {
    showToast(error.message);
  }
});

document.getElementById("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(event.target);
  try {
    const data = await api("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: formData.get("email"),
        password: formData.get("password"),
      }),
    });
    saveSession(data.token, data.user);
    await loadDocuments();
    await loadHistory();
    event.target.reset();
    showToast("Logged in");
  } catch (error) {
    showToast(error.message);
  }
});

document.getElementById("uploadForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData();
  const file = document.getElementById("fileInput").files[0];
  if (!file) {
    showToast("Choose a file");
    return;
  }
  formData.append("file", file);
  uploadMessage.textContent = "Uploading...";
  try {
    await api("/api/upload", {
      method: "POST",
      body: formData,
    });
    document.getElementById("fileInput").value = "";
    uploadMessage.textContent = "Upload complete.";
    await loadDocuments();
    showToast("File uploaded");
  } catch (error) {
    uploadMessage.textContent = error.message;
    showToast(error.message);
  }
});

document.getElementById("askForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = document.getElementById("questionInput").value.trim();
  if (!question) {
    showToast("Type a question");
    return;
  }
  answerBox.textContent = "Searching documents...";
  sourceList.innerHTML = "";
  try {
    const data = await api("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        documentId: documentFilter.value || null,
      }),
    });
    answerBox.textContent = data.answer;
    renderSources(data.sources || []);
    document.getElementById("questionInput").value = "";
    await loadHistory();
  } catch (error) {
    answerBox.textContent = error.message;
    showToast(error.message);
  }
});

document.getElementById("logoutBtn").addEventListener("click", () => {
  clearSession();
  answerBox.textContent = "Your answer will appear here.";
  sourceList.innerHTML = "";
});

(async function start() {
  if (!state.token) {
    switchView(false);
    return;
  }
  try {
    await loadProfile();
  } catch (error) {
    clearSession();
  }
})();
