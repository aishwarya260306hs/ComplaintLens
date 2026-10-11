# ComplaintLens
# Hacknetix 🚀
 AI-Powered Problem Aggregator & Smart Categorizer

> **Hacknetix** is an intelligent web application designed to bridge the gap between users and organizational grievance cells. By leveraging AI semantic analysis, it takes unstructured user problems written in everyday language, automatically categorizes them by meaning (e.g., Water & Sanitation, IT & Portal Issues), and maps them directly to specific institutions—ranging from top Bangalore universities to major tech firms and government municipal offices.

---

 🌟 Key Features

* **Multi-Stream Filtering:** Categorize issues seamlessly across multiple streams:
  * **Education:** Top universities and colleges in Bangalore (REVA University, PES University, RVCE, BMSCE, MS Ramaiah, Christ University, etc.).
  * **Tech Companies:** Leading IT enterprises (Infosys, Wipro, TCS, Accenture, Cognizant, IBM, Microsoft, Amazon).
  * **Government Offices:** Local civic bodies and municipal authorities (BBMP, BESCOM, BWSSB, RTO, Bangalore Police).
* **AI Semantic Problem Grouping:** Automatically clusters issues with identical core meanings regardless of wording (e.g., grouping "toilet water leakage" and "drinking water pipe issue" together under Water & Sanitation).
* **Personalized User Dashboard:** Track your engagement metrics with a live modal overview displaying:
  * Total problems posted
  * Successfully resolved complaints
  * On-going active issues
* **Clean & Modern UI:** Fully responsive, modern frontend designed with intuitive cards and navigation bars.

---

## 🛠️ Tech Stack

### **Frontend**
* **HTML5:** Semantic markup structure for accessibility and clarity.
* **CSS3:** Modern styling featuring Flexbox, CSS Variables, and responsive layouts.
* **JavaScript (ES6+):** Dynamic event handling, dependent dropdown logic, local state management, and real-time DOM updates.
* **FontAwesome:** Scalable vector icons for clean UI aesthetics.

**Backend & AI Architecture *(Recommended Integration Stack)***
* **Python / FastAPI or Flask:** Lightweight REST API framework to handle incoming user submissions.
* **NLP & Sentence Transformers:** For vectorizing text descriptions and performing cosine similarity clustering/grouping.
* **Database:** PostgreSQL or MongoDB to persistently store user profiles, organizations, and categorized complaint clusters.

---

 📂 Project Structure

```text
ComplaintLens/
│
├── index.html       # Main HTML layout, modal structures, and dashboard container
├── style.css        # Custom styling, variables, flexbox grids, and modal styles
└── script.js        # Stream/Organization mapping database, UI handlers, and AI grouping logic
