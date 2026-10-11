/* HACKNETIX shared module: API client, toasts, modal, helpers, nav */
(function () {
  "use strict";

  const API = {
    async request(url, opts = {}) {
      const meta = document.querySelector('meta[name="csrf-token"]');
      const headers = { Accept: "application/json", "X-CSRF-Token": meta ? meta.content : "" };
      if (!(opts.body instanceof FormData)) headers["Content-Type"] = "application/json";
      const res = await fetch(url, {
        credentials: "same-origin",
        ...opts,
        headers,
        body: opts.body && !(opts.body instanceof FormData) ? JSON.stringify(opts.body) : opts.body,
      });
      let data = null;
      try { data = await res.json(); } catch (_) {}
      if (!res.ok) {
        const err = new Error((data && (data.error || data.message)) || `Request failed (${res.status})`);
        err.status = res.status; err.data = data;
        if (res.status === 401) window.location.href = "/login?next=" + encodeURIComponent(location.pathname);
        throw err;
      }
      return data;
    },
    get(url) { return this.request(url); },
    post(url, body) { return this.request(url, { method: "POST", body }); },
    patch(url, body) { return this.request(url, { method: "PATCH", body }); },
    qs(obj) {
      const p = new URLSearchParams();
      Object.entries(obj).forEach(([k, v]) => { if (v !== "" && v != null) p.set(k, v); });
      const s = p.toString(); return s ? "?" + s : "";
    },
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const slug = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const fmtDate = (d) => d ? new Date(d).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—";
  const fmtDateTime = (d) => d ? new Date(d).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";
  const debounce = (fn, ms = 250) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

  const badge = (text, kind) => `<span class="badge ${slug(kind || text)}">${esc(text)}</span>`;
  const priorityBadge = (p) => badge(p ? p[0].toUpperCase() + p.slice(1) : "Unrated", p || "none");

  /* Toasts */
  function toast(msg, type = "success") {
    let box = $(".toasts");
    if (!box) { box = document.createElement("div"); box.className = "toasts"; box.setAttribute("aria-live", "polite"); document.body.appendChild(box); }
    const t = document.createElement("div");
    t.className = "toast " + type; t.setAttribute("role", "status"); t.textContent = msg;
    box.appendChild(t);
    setTimeout(() => { t.style.opacity = 0; t.style.transition = "opacity .3s"; setTimeout(() => t.remove(), 300); }, 3800);
  }

  /* Modal */
  function modal(html) {
    const back = document.createElement("div");
    back.className = "modal-back open";
    back.innerHTML = `<div class="modal" role="dialog" aria-modal="true">${html}</div>`;
    const close = () => { back.remove(); document.removeEventListener("keydown", onKey); };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    back.addEventListener("click", (e) => { if (e.target === back) close(); });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(back);
    const f = $("button, input, textarea, select", back); if (f) f.focus();
    return { el: back.firstElementChild, close };
  }

  const skeleton = (n = 3) => Array.from({ length: n }, () => '<div class="skel block" style="margin-bottom:12px"></div>').join("");
  const emptyState = (title, text, cta = "") => `
    <div class="empty">
      <svg viewBox="0 0 96 96" fill="none" aria-hidden="true"><circle cx="48" cy="48" r="44" fill="#e9eeff"/><rect x="26" y="28" width="44" height="40" rx="8" fill="#fff" stroke="#2f5bff" stroke-width="3"/><path d="M36 44h24M36 54h14" stroke="#7a3cff" stroke-width="3" stroke-linecap="round"/></svg>
      <h3>${esc(title)}</h3><p>${esc(text)}</p>${cta ? `<div style="margin-top:16px">${cta}</div>` : ""}
    </div>`;
  const errorState = (e) => emptyState("Something went wrong", e.message || "Please try again.");

  /* Inline form validation helper */
  function setFieldError(field, msg) {
    field.classList.toggle("invalid", !!msg);
    const err = $(".err", field); if (err) err.textContent = msg || "";
  }

  /* Nav */
  function initNav() {
    const toggle = $(".nav-toggle"), links = $(".nav-links");
    if (toggle && links) {
      toggle.addEventListener("click", () => {
        const open = links.classList.toggle("open");
        toggle.setAttribute("aria-expanded", open);
      });
    }
    const path = location.pathname;
    $$(".nav-links a").forEach((a) => {
      const href = a.getAttribute("href");
      if (href === path || (href !== "/" && path.startsWith(href.split("#")[0]) && href.split("#")[0] !== "/")) a.setAttribute("aria-current", "page");
    });
    // keep the remembered organization in sync with the signed-in account
    const b = document.body.dataset;
    if (b.auth === "1" && b.orgId) {
      const cur = Org.get();
      if (!cur || String(cur.id) !== b.orgId) Org.set({ id: Number(b.orgId), name: b.orgName, location: b.orgCity });
    }
    // notification badge
    const dot = $(".bell .dot");
    if (dot && document.body.dataset.auth === "1") {
      API.get("/api/notifications?unread=1").then((d) => {
        const n = (d.unread_count ?? (d.items || []).length) || 0;
        dot.textContent = n > 9 ? "9+" : n; dot.classList.toggle("show", n > 0);
      }).catch(() => {});
    }
  }

  /* Reveal-on-scroll (never blocks essential actions) */
  function initReveal() {
    if (!("IntersectionObserver" in window)) return;
    const io = new IntersectionObserver((es) => es.forEach((e) => {
      if (e.isIntersecting) { e.target.style.opacity = 1; e.target.style.transform = "none"; io.unobserve(e.target); }
    }), { threshold: .12 });
    $$("[data-reveal]").forEach((el) => {
      el.style.opacity = 0; el.style.transform = "translateY(16px)"; el.style.transition = "opacity .6s, transform .6s cubic-bezier(.2,.7,.2,1)";
      io.observe(el);
    });
  }

  /* Count-up animation */
  function countUp(el, to) {
    const dur = 700, start = performance.now();
    const step = (t) => { const p = Math.min((t - start) / dur, 1); el.textContent = Math.round(to * (1 - Math.pow(1 - p, 3))).toLocaleString(); if (p < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  }

  const STAGES = ["Submitted", "Under AI Analysis", "Awaiting Staff Review", "In Progress", "Resolved"];
  function stageIndex(status) {
    const s = slug(status);
    const i = STAGES.findIndex((x) => slug(x) === s);
    if (i >= 0) return i;
    if (s === "open") return 2;
    return 0;
  }

  /* Selected organization context (kept client-side only for UX; server enforces access) */
  const Org = {
    get() { try { return JSON.parse(localStorage.getItem("hx_org")) || null; } catch (_) { return null; } },
    set(o) { try { localStorage.setItem("hx_org", JSON.stringify(o)); } catch (_) {} },
  };

  window.HX = { API, $, $$, esc, slug, fmtDate, fmtDateTime, debounce, badge, priorityBadge, toast, modal, skeleton, emptyState, errorState, setFieldError, countUp, STAGES, stageIndex, Org };
  document.addEventListener("DOMContentLoaded", () => { initNav(); initReveal(); });
})();
