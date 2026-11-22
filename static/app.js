/* =============================================================================
 * Bot Engineering - Dashboard helper scripts (tema light, ikon Material Symbols)
 * ============================================================================= */
(function () {
  "use strict";

  /* ---------------------------------------------------------------- utilities */
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

  function el(sel) {
    return document.querySelector(sel);
  }

  function setText(sel, txt) {
    const node = el(sel);
    if (node) node.textContent = txt;
  }

  function icon(name, cls) {
    return '<span class="mi ' + (cls || "") + '">' + name + "</span>";
  }

  /* ------------------------------------------------------------------ toasts */
  const ICONS = { ok: "check_circle", err: "error", info: "info" };

  function toast(message, kind) {
    const wrap = el("#toast-wrap");
    if (!wrap) return;
    const type = kind || "info";
    const box = document.createElement("div");
    box.className = "toast toast-" + type;
    box.innerHTML =
      icon(ICONS[type] || "info", "text-brand-600") +
      '<div class="flex-1 text-sm text-slate-700"></div>' +
      '<button type="button" class="btn btn-ghost btn-icon !h-7 !w-7" aria-label="Tutup">' + icon("close", "!text-sm") + "</button>";
    box.querySelector("div").textContent = message;

    function close() {
      box.style.transition = "opacity .2s, transform .2s";
      box.style.opacity = "0";
      box.style.transform = "translateY(-6px)";
      setTimeout(() => box.remove(), 200);
    }
    box.querySelector("button").addEventListener("click", close);
    wrap.appendChild(box);
    setTimeout(close, type === "err" ? 8000 : 5000);
  }

  /* ------------------------------------------------------------------- modal */
  function openModal(title, html, iconName) {
    const modal = el("#modal");
    if (!modal) return;
    setText("#modal-title-text", title);
    const ico = el("#modal-title .mi");
    if (ico) ico.textContent = iconName || "info";
    const body = el("#modal-body");
    if (body) body.innerHTML = html;
    modal.classList.add("open");
  }

  function closeModal() {
    const modal = el("#modal");
    if (modal) modal.classList.remove("open");
  }

  document.addEventListener("click", function (ev) {
    if (ev.target.closest("[data-close-modal]")) closeModal();
    if (ev.target.id === "modal") closeModal();
  });

  /* ------------------------------------------------------------------ galeri */
  const gallery = {
    items: [],
    index: 0,
    detailUrl: "",
  };

  function galleryRender() {
    const items = gallery.items;
    if (!items.length) return;
    if (gallery.index < 0) gallery.index = items.length - 1;
    if (gallery.index >= items.length) gallery.index = 0;
    const item = items[gallery.index];
    const isVideo = /\.(mp4|webm|mov|m4v|ogg)$/i.test(item.url.split("?")[0]);

    const img = el("#gallery-image");
    const video = el("#gallery-video");
    if (img && video) {
      if (isVideo) {
        img.classList.add("hidden");
        video.classList.remove("hidden");
        if (video.getAttribute("src") !== item.url) {
          video.setAttribute("src", item.url);
          video.load();
        }
      } else {
        video.classList.add("hidden");
        video.removeAttribute("src");
        img.classList.remove("hidden");
        img.src = item.url;
        img.alt = item.name || "Foto ticket";
      }
    }
    setText("#gallery-counter", gallery.index + 1 + " / " + items.length);

    const openLink = el("#gallery-open");
    if (openLink) openLink.href = item.url;

    const dl = el("[data-gallery-download]");
    if (dl) dl.href = item.url + "&download=1";

    const detail = el("#gallery-detail");
    if (detail) {
      if (gallery.detailUrl) {
        detail.href = gallery.detailUrl;
        detail.hidden = false;
      } else {
        detail.hidden = true;
      }
    }

    const thumbs = el("#gallery-thumbs");
    if (thumbs) {
      thumbs.innerHTML = "";
      items.forEach(function (it, i) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "gallery-thumb" + (i === gallery.index ? " active" : "");
        b.title = it.name || "Foto " + (i + 1);
        if (/\.(mp4|webm|mov|m4v|ogg)$/i.test(it.url.split("?")[0])) {
          b.innerHTML =
            '<span class="mi flex h-full w-full items-center justify-center text-2xl text-slate-500">videocam</span>';
        } else {
          b.innerHTML = '<img src="' + it.url + '" alt="thumb">';
        }
        b.addEventListener("click", function () {
          gallery.index = i;
          galleryRender();
        });
        thumbs.appendChild(b);
      });
      const active = thumbs.querySelector(".gallery-thumb.active");
      if (active) active.scrollIntoView({ block: "nearest", inline: "center" });
    }
  }

  function openGallery(items, index, title, detailUrl) {
    gallery.items = items || [];
    gallery.index = index || 0;
    gallery.detailUrl = detailUrl || "";
    const modal = el("#gallery");
    if (!modal) return;
    setText("#gallery-title", title || "Foto ticket");
    galleryRender();
    modal.classList.add("open");
  }

  function closeGallery() {
    const modal = el("#gallery");
    if (modal) modal.classList.remove("open");
  }

  document.addEventListener("click", function (ev) {
    if (ev.target.closest("[data-close-gallery]")) {
      closeGallery();
      return;
    }
    if (ev.target.id === "gallery") {
      closeGallery();
      return;
    }
    if (ev.target.closest("[data-gallery-prev]")) {
      gallery.index -= 1;
      galleryRender();
      return;
    }
    if (ev.target.closest("[data-gallery-next]")) {
      gallery.index += 1;
      galleryRender();
      return;
    }

    /* pemicu galeri: elemen dengan data-gallery berisi JSON foto */
    const trigger = ev.target.closest("[data-gallery]");
    if (trigger) {
      let items = [];
      try {
        items = JSON.parse(trigger.getAttribute("data-gallery")) || [];
      } catch (e) {
        items = [];
      }
      if (!items.length) return; // biarkan link normal berjalan
      ev.preventDefault();
      openGallery(
        items,
        parseInt(trigger.getAttribute("data-gallery-index") || "0", 10),
        trigger.getAttribute("data-gallery-title") || "Foto ticket",
        trigger.getAttribute("data-gallery-detail") || ""
      );
    }
  });

  document.addEventListener("keydown", function (ev) {
    if (!el("#gallery") || !el("#gallery").classList.contains("open")) {
      if (ev.key === "Escape") closeModal();
      return;
    }
    if (ev.key === "Escape") closeGallery();
    else if (ev.key === "ArrowLeft") {
      gallery.index -= 1;
      galleryRender();
    } else if (ev.key === "ArrowRight") {
      gallery.index += 1;
      galleryRender();
    }
  });

  /* ------------------------------------------------- umpan balik form (POST) */
  // Form di halaman detail ticket (ubah status / PIC) harus memberi tanda
  // segera, kalau tidak tombolnya terlihat tidak merespon saat ditekan.
  document.addEventListener("submit", function (ev) {
    const form = ev.target;
    if (!form || form.tagName !== "FORM" || !form.classList.contains("js-submit")) return;
    const btn = form.querySelector("[type=submit]");
    if (!btn || btn.disabled) return;
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Menyimpan…';
    // Kalau halaman tidak berpindah (mis. validasi browser menahan submit),
    // kembalikan tombolnya setelah beberapa detik.
    window.setTimeout(function () {
      if (document.body.contains(btn)) {
        btn.disabled = false;
        btn.innerHTML = original;
      }
    }, 8000);
  });

  // Konfirmasi setelah redirect dari POST (?saved=status|pic)
  try {
    const saved = new URLSearchParams(location.search).get("saved");
    if (saved === "status") toast("Status ticket berhasil disimpan.", "ok");
    else if (saved === "pic") toast("PIC ticket berhasil disimpan.", "ok");
  } catch (e) {}

  /* ------------------------------------------------------------ foto hilang */
  // File media bisa hilang (dihapus manual / gagal unduh): ganti dengan
  // placeholder supaya tidak ada gambar rusak yang membingungkan.
  document.addEventListener(
    "error",
    function (ev) {
      const img = ev.target;
      if (!img || img.tagName !== "IMG" || img.dataset.fallback) return;
      const figure = img.closest("figure");
      if (!figure) return;
      img.dataset.fallback = "1";
      const holder = document.createElement("div");
      holder.className =
        "flex h-52 w-full items-center justify-center rounded-xl border border-dashed border-slate-300 bg-slate-50 text-xs text-slate-500";
      holder.textContent = "Foto tidak tersedia";
      if (img.parentNode) img.parentNode.insertBefore(holder, img);
      img.style.display = "none";
    },
    true
  );

  /* ------------------------------------------------------------ api actions */
  const actions = {
    "waha-start": () =>
      post("/api/waha/start").then((r) => "Session: " + JSON.stringify(r.result || r.error || r)),

    "waha-status": () =>
      post("/api/waha/status").then(
        (r) => "Status: " + (r.status || "-") + " | " + JSON.stringify(r.sessions || r.error || "")
      ),

    "waha-connect": () =>
      post("/api/waha/connect").then((r) => {
        if (!r.ok) throw new Error(r.error || "tidak diketahui");
        const steps = (r.steps || []).join(" · ");
        const hook = r.webhook_ok ? "webhook sinkron" : "webhook belum sinkron";
        return (
          "Terhubung: " + (r.base_url || "-") + " | status " + (r.status || "-") +
          " | " + hook + (steps ? " (" + steps + ")" : "")
        );
      }),

    "waha-sync": () =>
      post("/api/waha/sync").then((r) => {
        if (!r.ok) throw new Error(r.error || "-");
        return "Sinkron OK — status: " + (r.status || "-") +
          " | webhook: " + (r.webhook_ok ? "sinkron" : "belum sinkron");
      }),

    "waha-detect": () =>
      get("/api/waha/detect").then((r) => {
        if (!r.ok) throw new Error(r.error || "-");
        const input = el('input[name="waha_base_url"]');
        if (input) input.value = r.url;
        return "WAHA ditemukan di " + r.url + " (disimpan ke form, tekan Simpan)";
      }),

    "waha-restart": () =>
      post("/api/waha/restart").then((r) => {
        if (!r.ok) throw new Error(r.error || "-");
        return "Session WAHA di-restart.";
      }),

    "waha-webhook-clean": () =>
      post("/api/waha/webhooks/clean").then((r) => {
        if (!r.ok) throw new Error(r.error || "-");
        return "Webhook dihapus: " + JSON.stringify(r.removed || []);
      }),

    "waha-qr": () =>
      get("/api/waha/qr").then((r) => {
        const box = el("#qr-box");
        let imgTag = "";
        if (r.qr) {
          imgTag =
            '<img src="data:' + (r.mimetype || "image/png") + ";base64," + r.qr +
            '" alt="QR pairing WhatsApp" class="mx-auto">';
        }
        if (box) {
          box.innerHTML = imgTag
            ? '<div class="rounded-xl border border-slate-200 bg-white p-4">' + imgTag + "</div>"
            : "";
        }
        if (!r.ok) throw new Error("QR belum tersedia: " + (r.error || "-"));
        openModal(
          "Scan QR WhatsApp",
          '<p class="mb-3">Buka WhatsApp di HP &rarr; <strong>Perangkat tertaut</strong> &rarr; pindai QR ini.</p>' +
            (imgTag || "") +
            '<p class="mt-3 text-xs text-slate-500">Jika QR sudah dipakai, gunakan tombol Restart session untuk membuat QR baru.</p>',
          "qr_code_2"
        );
        return "Scan QR ini dengan WhatsApp (HP → Perangkat tertaut).";
      }),

    "waha-webhook": () =>
      post("/api/waha/webhook").then((r) => {
        if (!r.ok) throw new Error(r.error || "-");
        return "Webhook didaftarkan: " + JSON.stringify(r.webhooks || r.result || "");
      }),

    "sheets-test": () =>
      post("/api/sheets/test").then((r) => {
        if (!r.ok) throw new Error(r.error || "tidak diketahui");
        return "Koneksi Sheets OK";
      }),

    "sheets-sync": () =>
      post("/api/sheets/sync").then((r) => {
        if (!r.ok) throw new Error(r.error || "-");
        return r.synced + " ticket disinkron";
      }),

    "public-url-detect": () =>
      get("/api/public-url/detect").then((r) => {
        const input = el('input[name="bot_public_url"]');
        if (input && r.url) input.value = r.url;
        if (!r.url) throw new Error(r.error || "-");
        return (
          "Alamat server: " + r.url +
          ((r.candidates || []).length ? " (pilihan: " + r.candidates.join("  ·  ") + ")" : "") +
          (r.note ? " — " + r.note : "")
        );
      }),
  };

  // Elemen output per kelompok aksi
  const OUT_TARGET = {
    "sheets-test": "#sheets-out",
    "sheets-sync": "#sheets-out",
    "public-url-detect": null,
  };

  function outFor(action) {
    if (action in OUT_TARGET) return OUT_TARGET[action];
    return "#waha-out";
  }

  /* ------------------------------------------------- umpan balik aksi (inline) */
  // Setiap bagian yang punya tombol aksi bisa menaruh <p data-status="...">.
  // Hasil aksi ditulis di situ (bukan jauh di bawah), plus toast di pojok.
  function setStatus(scope, kind, message, iconName) {
    const box = scope && scope.querySelector ? scope.querySelector("[data-status]") : null;
    if (!box) return;
    box.className = "action-status show " + (kind || "");
    box.innerHTML = icon(iconName || (kind === "err" ? "error" : kind === "ok" ? "check_circle" : "info"));
    const span = document.createElement("span");
    span.textContent = message;
    box.appendChild(span);
  }

  function clearStatus(scope) {
    const box = scope && scope.querySelector ? scope.querySelector("[data-status]") : null;
    if (box) box.className = "action-status";
  }

  document.addEventListener("click", function (ev) {
    const btn = ev.target.closest("[data-action]");
    if (btn) {
      const action = btn.dataset.action;
      const fn = actions[action];
      if (!fn) return;
      ev.preventDefault();

      const scope = btn.closest(".card, .integrasi, section") || document;
      const target = el(outFor(action));
      const original = btn.innerHTML;

      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span>' + original.replace(/<span class="mi">[^<]*<\/span>\s*/, "");
      if (target) target.textContent = "Memproses...";
      setStatus(scope, "busy", "Memproses…", "sync");

      fn()
        .then((txt) => {
          if (target) target.textContent = txt;
          setStatus(scope, "ok", txt);
          toast(txt, "ok");
          if (el("#waha-info")) refreshWaha();
        })
        .catch((e) => {
          const msg = e && e.message ? e.message : String(e);
          if (target) target.textContent = "Error: " + msg;
          setStatus(scope, "err", "Gagal: " + msg);
          toast(msg, "err");
        })
        .finally(() => {
          btn.disabled = false;
          btn.innerHTML = original;
        });
      return;
    }

    const removeButton = ev.target.closest(".rm-row");
    if (removeButton) {
      const tr = removeButton.closest("tr");
      if (tr) {
        const table = tr.closest("table");
        tr.remove();
        if (table && (table.id === "cmd-table" || table.id === "st-table")) {
          reindexRows(table);
        }
        const form = el("#settings-form");
        if (form) form.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }
  });

  /* ------------------------------------------------------------ tabel dinamis */
  function addRow(tableId, html, rowIndex) {
    const tbody = el(tableId + " tbody");
    if (!tbody) return;
    const tr = document.createElement("tr");
    tr.dataset.rowIndex = String(rowIndex);
    tr.innerHTML = html;
    tbody.appendChild(tr);
  }

  function reindexRows(table) {
    const fieldPrefix = table.id === "cmd-table" ? "cmd_enabled_" : "st_open_";
    table.querySelectorAll("tbody tr").forEach(function (tr, index) {
      tr.dataset.rowIndex = String(index);
      const checkbox = tr.querySelector('input[type="checkbox"]');
      if (checkbox) checkbox.name = fieldPrefix + index;
    });
  }

  const cmdBtn = el("#add-cmd");
  if (cmdBtn) {
    cmdBtn.addEventListener("click", function () {
      const rowIndex = el("#cmd-table tbody").querySelectorAll("tr").length;
      addRow(
        "#cmd-table",
        '<td><input type="text" name="cmd_name" value="" class="input mt-0"></td>' +
          '<td><input type="text" name="cmd_aliases" value="" class="input mt-0"></td>' +
          '<td><select name="cmd_action" class="input mt-0">' +
          '<option>create</option><option>status</option><option>pic</option>' +
          '<option>close</option><option>cancel</option><option>list</option>' +
          '<option>info</option><option>help</option></select></td>' +
          '<td><input type="text" name="cmd_usage" value="" class="input mt-0"></td>' +
          '<td><input type="text" name="cmd_description" value="" class="input mt-0"></td>' +
          '<td class="text-center"><input type="checkbox" name="cmd_enabled_' + rowIndex + '" checked class="h-4 w-4 rounded border-slate-300 text-brand-600"></td>' +
          '<td><button type="button" class="btn btn-danger btn-sm rm-row" title="Hapus baris">' + icon("close", "!text-sm") + "</button></td>",
        rowIndex
      );
      const form = el("#settings-form");
      if (form) form.dispatchEvent(new Event("change", { bubbles: true }));
      toast("Perintah baru ditambahkan. Isi kolomnya lalu klik Simpan Pengaturan.", "info");
    });
  }

  const stBtn = el("#add-st");
  if (stBtn) {
    stBtn.addEventListener("click", function () {
      const rowIndex = el("#st-table tbody").querySelectorAll("tr").length;
      addRow(
        "#st-table",
        '<td><input type="text" name="st_key" value="" class="input mt-0"></td>' +
          '<td><input type="text" name="st_label" value="" class="input mt-0"></td>' +
          '<td class="text-center"><input type="checkbox" name="st_open_' + rowIndex + '" checked class="h-4 w-4 rounded border-slate-300 text-brand-600"></td>' +
          '<td><input type="text" name="st_transitions" value="" class="input mt-0"></td>' +
          '<td><input type="text" name="st_color" value="#e2e8f0" class="input mt-0 w-28"></td>' +
          '<td><button type="button" class="btn btn-danger btn-sm rm-row" title="Hapus baris">' + icon("close", "!text-sm") + "</button></td>",
        rowIndex
      );
      const form = el("#settings-form");
      if (form) form.dispatchEvent(new Event("change", { bubbles: true }));
      toast("Status baru ditambahkan. Isi kolomnya lalu klik Simpan Pengaturan.", "info");
    });
  }

  /* ---------------------------------------------------------------- salin URL */
  const copyBtn = el("#copy-webhook");
  if (copyBtn) {
    copyBtn.addEventListener("click", function () {
      const field = el("#webhook-url");
      if (!field) return;
      field.select();
      const done = () => {
        const old = copyBtn.innerHTML;
        copyBtn.innerHTML = icon("check", "!text-sm") + " Tersalin!";
        toast("URL webhook disalin ke clipboard.", "ok");
        setTimeout(() => (copyBtn.innerHTML = old), 1500);
      };
      const fallbackCopy = () => {
        field.focus();
        field.select();
        let copied = false;
        try {
          copied = document.execCommand("copy");
        } catch (e) {}
        if (copied) done();
        else toast("Tidak dapat menyalin URL. Silakan salin secara manual.", "err");
      };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(field.value).then(done, fallbackCopy);
      } else {
        fallbackCopy();
      }
    });
  }

  /* ------------------------------------------------------------- auto refresh */
  function refreshWaha() {
    return get("/api/waha/status")
      .then((r) => {
        if (!r || !r.status) return;
        setText("#waha-status", r.status);
        const hook = el("#waha-webhook");
        if (hook) {
          hook.innerHTML = r.webhook_ok
            ? icon("check_circle", "text-emerald-600") + " sinkron"
            : icon("warning", "text-amber-600") + " belum sinkron";
        }
        const auto = el("#waha-auto");
        if (auto) {
          auto.innerHTML = r.auto_sync
            ? icon("autorenew", "text-emerald-600") + " aktif"
            : icon("pause", "text-slate-400") + " nonaktif";
        }
        setText("#waha-checked", r.checked_at || "-");
        setText("#waha-pill-text", r.status);
        const pill = el("#waha-pill");
        if (pill) {
          pill.classList.remove("pill-ok", "pill-bad");
          pill.classList.add(r.ok ? "pill-ok" : "pill-bad");
        }
        const linkIcon = el("#waha-link-icon");
        if (linkIcon) {
          linkIcon.classList.remove("ok", "bad");
          linkIcon.classList.add(r.ok ? "ok" : "bad");
        }
      })
      .catch(function () {});
  }

  if (el("#waha-info")) {
    setInterval(refreshWaha, 10000);
  }

  /* ---------------------------------------------------------------- navigasi */
  const sidebar = el("#sidebar");
  document.addEventListener("click", function (ev) {
    if (ev.target.closest("[data-open-sidebar]") && sidebar) sidebar.classList.add("open");
    if (ev.target.closest("[data-close-sidebar]") && sidebar) sidebar.classList.remove("open");
    if (ev.target.classList.contains("sidebar-backdrop")) sidebar.classList.remove("open");
  });

  // Tab (Pengaturan & detail ticket)
  document.addEventListener("click", function (ev) {
    const tab = ev.target.closest("[data-tab]");
    if (!tab) return;
    const targetId = tab.dataset.tab;
    document.querySelectorAll("[data-tab]").forEach(function (b) {
      b.classList.toggle("active", b.dataset.tab === targetId);
    });
    let activePanel = null;
    document.querySelectorAll(".tab-panel").forEach(function (p) {
      const on = p.id === targetId;
      p.classList.toggle("active", on);
      if (on) activePanel = p;
    });
    try {
      localStorage.setItem("bot-tab:" + location.pathname, targetId);
    } catch (e) {}
    // Tampilkan panel yang baru dipilih: scroll ke atas panel, tepat di bawah
    // header lengket. Tanpa ini pengguna mengira tab tidak merespon, terutama
    // kalau halaman sedang scroll ke bawah.
    if (activePanel) {
      const header = document.querySelector("header");
      const offset = header ? header.getBoundingClientRect().height + 8 : 56;
      const top = activePanel.getBoundingClientRect().top + window.scrollY - offset;
      window.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
    }
  });

  try {
    const saved = localStorage.getItem("bot-tab:" + location.pathname);
    if (saved) {
      const btn = document.querySelector('[data-tab="' + saved + '"]');
      if (btn) btn.click();
    }
  } catch (e) {}

  /* ---------------------------------------------------------- pencarian tabel */
  document.querySelectorAll("[data-table-search]").forEach(function (input) {
    const table = el("#" + input.dataset.tableSearch);
    if (!table) return;
    const status = el("[data-search-status]");

    function apply() {
      const q = input.value.trim().toLowerCase();
      let shown = 0;
      const rows = table.querySelectorAll("tbody tr[data-row-text]");
      rows.forEach(function (tr) {
        const match = !q || (tr.dataset.rowText || "").toLowerCase().indexOf(q) !== -1;
        tr.style.display = match ? "" : "none";
        if (match) shown++;
      });
      if (status) {
        status.textContent = q
          ? shown + " dari " + rows.length + " baris cocok dengan \"" + input.value.trim() + "\""
          : rows.length + " baris ditampilkan";
      }
    }

    input.addEventListener("input", apply);
    apply();
  });

  /* ------------------------------------------------------- auto-refresh log */
  const liveBox = el("#live-reload");
  if (liveBox) {
    let timer = null;
    liveBox.addEventListener("change", function () {
      if (liveBox.checked) {
        timer = setInterval(function () {
          location.reload();
        }, 10000);
        toast("Auto-refresh aktif (reload setiap 10 detik)", "info");
      } else if (timer) {
        clearInterval(timer);
        timer = null;
        toast("Auto-refresh dimatikan", "info");
      }
    });
  }

  /* --------------------------------------------- peringatan form belum disimpan */
  const settingsForm = el("#settings-form");
  if (settingsForm) {
    let dirty = false;
    const dirtyBadge = el("#dirty-badge");
    const saveBtn = el("#save-settings");

    function markDirty(state) {
      dirty = state;
      if (dirtyBadge) dirtyBadge.classList.toggle("show", state);
      if (saveBtn) saveBtn.classList.toggle("ring-2", state);
    }

    settingsForm.addEventListener("input", function () {
      markDirty(true);
      validatePublicUrl();
    });
    settingsForm.addEventListener("change", function () {
      markDirty(true);
    });

    settingsForm.addEventListener("submit", function (ev) {
      const btn = ev.submitter || saveBtn;
      if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Menyimpan…';
      }
      markDirty(false);
      // Jangan ditahan oleh beforeunload saat sedang menyimpan.
      setTimeout(function () {
        markDirty(false);
      }, 1500);
    });

    // Ctrl+S / Cmd+S = simpan pengaturan
    document.addEventListener("keydown", function (ev) {
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s") {
        ev.preventDefault();
        if (saveBtn && !saveBtn.disabled) settingsForm.requestSubmit
          ? settingsForm.requestSubmit()
          : saveBtn.click();
      }
    });

    window.addEventListener("beforeunload", function (ev) {
      if (!dirty) return;
      ev.preventDefault();
      ev.returnValue = "";
    });

    // Peringatan URL publik: tampilkan langsung saat field diisi.
    const urlField = el('input[name="bot_public_url"]');
    const urlWarning = el("#public-url-warning");
    function validatePublicUrl() {
      if (!urlField || !urlWarning) return;
      const v = (urlField.value || "").trim();
      const bad = !v || v.indexOf("app:8080") !== -1 || v.indexOf("localhost") !== -1 || v.indexOf("127.0.0.1") !== -1;
      urlWarning.hidden = !bad;
    }
    urlField && urlField.addEventListener("input", validatePublicUrl);
    validatePublicUrl();

    // Konfirmasi setelah halaman dimuat ulang (?saved=1)
    if (new URLSearchParams(location.search).get("saved")) {
      toast("Pengaturan tersimpan dan langsung dipakai bot.", "ok");
    }

    // Buang perubahan -> muat ulang nilai tersimpan
    const discard = el("#discard-settings");
    if (discard) {
      discard.addEventListener("click", function () {
        if (!dirty) {
          toast("Tidak ada perubahan yang perlu dibuang.", "info");
          return;
        }
        if (confirm("Buang semua perubahan yang belum disimpan?")) {
          markDirty(false);
          location.reload();
        }
      });
    }
  }
})();
