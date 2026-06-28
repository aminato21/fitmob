(() => {
  const root = document.documentElement;
  const storedTheme = localStorage.getItem("runstead-theme");
  root.dataset.theme = storedTheme || "dark";

  const themeButton = document.querySelector("[data-theme-toggle]");
  if (themeButton) {
    const updateLabel = () => {
      const light = root.dataset.theme === "light";
      themeButton.setAttribute("aria-label", light ? "Use dark theme" : "Use light theme");
      themeButton.dataset.mode = light ? "light" : "dark";
    };
    updateLabel();
    themeButton.addEventListener("click", () => {
      root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
      localStorage.setItem("runstead-theme", root.dataset.theme);
      updateLabel();
      window.dispatchEvent(new Event("runstead-theme-change"));
    });
  }

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.querySelectorAll("[data-count]").forEach((node) => {
    const target = Number(node.dataset.count);
    const decimals = Number(node.dataset.decimals || 0);
    if (!Number.isFinite(target) || reduced) {
      node.textContent = target.toFixed(decimals);
      return;
    }
    const start = performance.now();
    const animate = (now) => {
      const progress = Math.min(1, (now - start) / 750);
      const eased = 1 - Math.pow(1 - progress, 3);
      node.textContent = (target * eased).toFixed(decimals);
      if (progress < 1) requestAnimationFrame(animate);
    };
    requestAnimationFrame(animate);
  });

  const revealObserver = "IntersectionObserver" in window
    ? new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            revealObserver.unobserve(entry.target);
          }
        });
      }, { threshold: 0.08 })
    : null;
  document.querySelectorAll(".reveal").forEach((node) => {
    if (revealObserver && !reduced) revealObserver.observe(node);
    else node.classList.add("is-visible");
  });

  const palette = () => {
    const styles = getComputedStyle(root);
    return {
      text: styles.getPropertyValue("--muted").trim(),
      line: styles.getPropertyValue("--line").trim(),
      teal: styles.getPropertyValue("--teal").trim(),
      cyan: styles.getPropertyValue("--cyan").trim(),
      violet: styles.getPropertyValue("--violet").trim(),
      coral: styles.getPropertyValue("--coral").trim(),
    };
  };

  const readData = (id) => {
    const node = document.getElementById(id);
    if (!node) return [];
    try { return JSON.parse(node.textContent); } catch (_) { return []; }
  };

  const drawChart = (canvas, data, kind = "bar", reverse = false) => {
    if (!canvas || !data.length) return;
    const ctx = canvas.getContext("2d");
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth || 320;
    const height = canvas.clientHeight || 180;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    ctx.scale(ratio, ratio);
    const colors = palette();
    const pad = { left: 34, right: 10, top: 14, bottom: 28 };
    const chartW = width - pad.left - pad.right;
    const chartH = height - pad.top - pad.bottom;
    const values = data.map((item) => Number(item.value)).filter(Number.isFinite);
    if (!values.length) return;
    const min = kind === "bar" ? 0 : Math.min(...values) * .96;
    const max = Math.max(...values) * 1.04 || 1;
    const y = (value) => {
      const normal = (value - min) / Math.max(.001, max - min);
      return pad.top + (reverse ? normal : 1 - normal) * chartH;
    };
    ctx.clearRect(0, 0, width, height);
    ctx.strokeStyle = colors.line;
    ctx.fillStyle = colors.text;
    ctx.font = "10px Inter, sans-serif";
    ctx.lineWidth = 1;
    for (let i = 0; i < 4; i++) {
      const gy = pad.top + (chartH / 3) * i;
      ctx.beginPath(); ctx.moveTo(pad.left, gy); ctx.lineTo(width - pad.right, gy); ctx.stroke();
    }
    if (kind === "bar") {
      const slot = chartW / data.length;
      const barW = Math.max(7, slot * .58);
      const gradient = ctx.createLinearGradient(0, pad.top, 0, pad.top + chartH);
      gradient.addColorStop(0, colors.teal);
      gradient.addColorStop(1, colors.cyan);
      data.forEach((item, index) => {
        const value = Number(item.value) || 0;
        const x = pad.left + index * slot + (slot - barW) / 2;
        const top = y(value);
        ctx.fillStyle = gradient;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(x, top, barW, pad.top + chartH - top, 5);
        else ctx.rect(x, top, barW, pad.top + chartH - top);
        ctx.fill();
      });
    } else {
      const step = data.length > 1 ? chartW / (data.length - 1) : chartW;
      const gradient = ctx.createLinearGradient(pad.left, 0, width - pad.right, 0);
      gradient.addColorStop(0, colors.violet);
      gradient.addColorStop(1, kind === "walk" ? colors.coral : colors.cyan);
      ctx.strokeStyle = gradient; ctx.lineWidth = 3; ctx.lineJoin = "round";
      ctx.beginPath();
      data.forEach((item, index) => {
        const px = pad.left + index * step;
        const py = y(Number(item.value));
        if (index === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      });
      ctx.stroke();
      data.forEach((item, index) => {
        ctx.fillStyle = colors.violet;
        ctx.beginPath(); ctx.arc(pad.left + index * step, y(Number(item.value)), 3.5, 0, Math.PI * 2); ctx.fill();
      });
    }
    const labelIndexes = data.length <= 5 ? data.map((_, i) => i) : [0, Math.floor(data.length / 2), data.length - 1];
    ctx.fillStyle = colors.text; ctx.textAlign = "center";
    labelIndexes.forEach((index) => {
      const x = pad.left + (data.length === 1 ? chartW / 2 : (chartW / Math.max(1, data.length - (kind === "bar" ? 0 : 1))) * (kind === "bar" ? index + .5 : index));
      const raw = String(data[index].label || "");
      ctx.fillText(raw.length > 8 ? raw.slice(5) : raw, x, height - 8);
    });
  };

  const drawAll = () => {
    drawChart(document.getElementById("weekly-chart"), readData("weekly-chart-data"), "bar");
    drawChart(document.getElementById("pace-chart"), readData("pace-chart-data"), "line", true);
    drawChart(document.getElementById("walk-chart"), readData("walk-chart-data"), "walk", true);
  };
  requestAnimationFrame(drawAll);
  window.addEventListener("resize", drawAll);
  window.addEventListener("runstead-theme-change", drawAll);

  document.querySelectorAll("[data-week-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      const week = button.dataset.weekTab;
      document.querySelectorAll("[data-week-tab]").forEach((item) => item.classList.toggle("active", item === button));
      document.querySelectorAll("[data-week-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.weekPanel === week));
    });
  });

  document.querySelectorAll("[data-loading-form]").forEach((form) => {
    form.addEventListener("submit", () => {
      const overlay = document.querySelector(".loading-overlay");
      if (overlay) overlay.classList.add("active");
      const button = form.querySelector("button[type=submit]");
      if (button) { button.disabled = true; button.textContent = button.dataset.loadingText || "Working…"; }
    });
  });

  const dropzone = document.querySelector("[data-dropzone]");
  const fileInput = document.querySelector("[data-file-input]");
  const fileLabel = document.querySelector("[data-file-label]");
  if (dropzone && fileInput) {
    ["dragenter", "dragover"].forEach((eventName) => dropzone.addEventListener(eventName, (event) => { event.preventDefault(); dropzone.classList.add("dragging"); }));
    ["dragleave", "drop"].forEach((eventName) => dropzone.addEventListener(eventName, (event) => { event.preventDefault(); dropzone.classList.remove("dragging"); }));
    dropzone.addEventListener("drop", (event) => {
      if (event.dataTransfer.files.length) {
        fileInput.files = event.dataTransfer.files;
        if (fileLabel) fileLabel.textContent = event.dataTransfer.files[0].name;
      }
    });
    fileInput.addEventListener("change", () => {
      if (fileLabel && fileInput.files.length) fileLabel.textContent = fileInput.files[0].name;
    });
  }

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
  }
})();

