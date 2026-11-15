/* Dashboard helper scripts */
(function () {
  "use strict";

  function post(url, body) {
    const opts = { method: "POST" };
    if (body) {
      opts.headers = { "Content-Type": "application/x-www-form-urlencoded" };
      opts.body = body;
    }
    return fetch(url, opts).then((r) => r.json().catch(() => ({ ok: false })));
  }

  function get(url) {
    return fetch(url).then((r) => r.json().catch(() => ({ ok: false })));
  }

  function token() {
    const el = document.querySelector('input[name="waha_webhook_token"]');
    return el ? el.value.trim() : "";
  }

  const actions = {
    "waha-start": () => {
      return post("/api/waha/start").then((r) =>
        "Session: " + JSON.stringify(r.result || r.error || r)
      );
    },
    "waha-status": () => {
      return post("/api/waha/status").then((r) =>
        "Status: " + (r.status || "-") + " | " + JSON.stringify(r.sessions || r.error || "")
      );
    },
    "waha-connect": () =>
      post("/api/waha/connect").then((r) => {
        if (!r.ok) return "Gagal: " + (r.error || "tidak diketahui");
        const steps = (r.steps || []).join(" · ");
        const hook = r.webhook_ok ? "webhook sinkron" : "webhook belum sinkron";
        return (
          "Terhubung: " + (r.base_url || "-") + " | status " + (r.status || "-") +
          " | " + hook + (steps ? " (" + steps + ")" : "")
        );
      }),
    "waha-sync": () =>
      post("/api/waha/sync").then((r) =>
        r.ok
          ? "Sinkron OK — status: " + (r.status || "-") +
            " | webhook: " + (r.webhook_ok ? "sinkron" : "belum sinkron")
          : "Gagal: " + (r.error || "-")
      ),
    "waha-detect": () =>
      get("/api/waha/detect").then((r) => {
        if (!r.ok) return "WAHA tidak ditemukan: " + (r.error || "-");
        const input = document.querySelector('input[name="waha_base_url"]');
        if (input) input.value = r.url;
        return "WAHA ditemukan di " + r.url + " (disimpan ke form, tekan Simpan)";
      }),
    "waha-restart": () =>
      post("/api/waha/restart").then((r) =>
        r.ok ? "Session WAHA di-restart." : "Gagal: " + (r.error || "-")
      ),
    "waha-webhook-clean": () =>
      post("/api/waha/webhooks/clean").then((r) =>
        r.ok
          ? "Webhook dihapus: " + JSON.stringify(r.removed || [])
          : "Gagal: " + (r.error || "-")
      ),
    "waha-qr": () => {
      return get("/api/waha/qr").then((r) => {
        const box = document.getElementById("qr-box");
        if (box) {
          box.innerHTML = "";
          if (r.qr) {
            const img = document.createElement("img");
            img.src = "data:" + (r.mimetype || "image/png") + ";base64," + r.qr;
            img.alt = "QR pairing WhatsApp";
            box.appendChild(img);
          }
        }
        return r.ok
          ? "Scan QR ini dengan WhatsApp (HP → Perangkat tertaut)."
          : "QR belum tersedia: " + (r.error || "-");
      });
    },
    "waha-webhook": () => {
      // URL disusun server-side (sudah termasuk token) - jangan kirim token dari browser.
      return post("/api/waha/webhook").then((r) =>
        r.ok
          ? "Webhook didaftarkan: " + JSON.stringify(r.webhooks || r.result || "")
          : "Gagal: " + (r.error || "-")
      );
    },
    "sheets-test": () =>
      post("/api/sheets/test").then((r) =>
        r.ok ? "Koneksi Sheets OK" : "Gagal: " + (r.error || "tidak diketahui")
      ),
    "sheets-sync": () =>
      post("/api/sheets/sync").then((r) =>
        r.ok ? r.synced + " ticket disinkron" : "Gagal: " + (r.error || "-")
      ),
    "public-url-detect": () =>
      get("/api/public-url/detect").then((r) => {
        const input = document.querySelector('input[name="bot_public_url"]');
        if (input && r.url) input.value = r.url;
        return r.url
          ? "Alamat server: " + r.url +
            ((r.candidates || []).length ? " (pilihan: " + r.candidates.join("  ·  ") + ")" : "") +
            (r.note ? " — " + r.note : "")
          : "Tidak bisa dideteksi: " + (r.error || "-");
      }),
  };

  document.addEventListener("click", function (ev) {
    const btn = ev.target.closest("[data-action]");
    if (btn) {
      const fn = actions[btn.dataset.action];
      if (!fn) return;
      const out = document.querySelector("#waha-out, #sheets-out");
      if (out) out.textContent = "Memproses...";
      fn()
        .then((txt) => {
          if (out) out.textContent = txt;
          if (document.querySelector("#waha-info")) refreshWaha();
        })
        .catch((e) => {
          if (out) out.textContent = "Error: " + e;
        });
      return;
    }

    if (ev.target.classList.contains("rm-row")) {
      const tr = ev.target.closest("tr");
      if (tr) tr.remove();
    }
  });

  function addRow(tableId, html) {
    const tbody = document.querySelector(tableId + " tbody");
    if (!tbody) return;
    const tr = document.createElement("tr");
    tr.innerHTML = html;
    tbody.appendChild(tr);
  }

  const cmdBtn = document.getElementById("add-cmd");
  if (cmdBtn) {
    cmdBtn.addEventListener("click", function () {
      addRow(
        "#cmd-table",
        `<td><input type="text" name="cmd_name" value=""></td>
         <td><input type="text" name="cmd_aliases" value=""></td>
         <td><select name="cmd_action">
              <option>create</option><option>status</option><option>pic</option>
              <option>close</option><option>cancel</option><option>list</option>
              <option>info</option><option>help</option></select></td>
         <td><input type="text" name="cmd_usage" value=""></td>
         <td><input type="text" name="cmd_description" value=""></td>
         <td class="center"><input type="checkbox" name="cmd_enabled" checked></td>
         <td><button type="button" class="btn small danger rm-row">✕</button></td>`
      );
    });
  }

  const stBtn = document.getElementById("add-st");
  if (stBtn) {
    stBtn.addEventListener("click", function () {
      addRow(
        "#st-table",
        `<td><input type="text" name="st_key" value=""></td>
         <td><input type="text" name="st_label" value=""></td>
         <td class="center"><input type="checkbox" name="st_open" checked></td>
         <td><input type="text" name="st_transitions" value=""></td>
         <td><input type="text" name="st_color" value="#e2e8f0"></td>
         <td><button type="button" class="btn small danger rm-row">✕</button></td>`
      );
    });
  }

  const copyBtn = document.getElementById("copy-webhook");
  if (copyBtn) {
    copyBtn.addEventListener("click", function () {
      const field = document.getElementById("webhook-url");
      if (!field) return;
      field.select();
      navigator.clipboard
        ? navigator.clipboard.writeText(field.value)
        : document.execCommand("copy");
      copyBtn.textContent = "Tersalin!";
      setTimeout(() => (copyBtn.textContent = "Salin"), 1500);
    });
  }

  // -------------------------------------------------------------------------
  // Auto-refresh status WAHA (dashboard ikut berubah tanpa refresh manual)
  // -------------------------------------------------------------------------
  function setText(sel, txt) {
    const el = document.querySelector(sel);
    if (el) el.textContent = txt;
  }

  function refreshWaha() {
    return get("/api/waha/status")
      .then((r) => {
        if (!r || !r.status) return;
        setText("#waha-status", r.status);
        setText("#waha-webhook", r.webhook_ok ? "✅ sinkron" : "⚠️ belum sinkron");
        setText("#waha-checked", r.checked_at || "-");
        setText("#waha-auto", r.auto_sync ? "aktif" : "nonaktif");
        setText("#waha-pill-text", r.status);
        const pill = document.querySelector("#waha-pill");
        if (pill) {
          pill.classList.remove("ok", "bad");
          pill.classList.add(r.ok ? "ok" : "bad");
        }
      })
      .catch(function () {});
  }

  if (document.querySelector("#waha-info")) {
    setInterval(refreshWaha, 10000);
  }
})();
