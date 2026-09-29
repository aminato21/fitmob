(() => {
  const root = document.documentElement;
  const storedTheme = localStorage.getItem("runstead-theme");
  root.dataset.theme = storedTheme || "dark";

  const themeButton = document.querySelector("[data-theme-toggle]");
  const themeColor = document.querySelector('meta[name="theme-color"]');
  if (themeButton) {
    const updateLabel = () => {
      const light = root.dataset.theme === "light";
      themeButton.setAttribute("aria-label", light ? "Use dark theme" : "Use light theme");
      themeButton.dataset.mode = light ? "light" : "dark";
      if (themeColor) themeColor.content = light ? "#eeeae0" : "#080c0e";
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
    const suffix = node.dataset.suffix || "";
    if (!Number.isFinite(target) || reduced) {
      node.textContent = `${target.toFixed(decimals)}${suffix}`;
      return;
    }
    const start = performance.now();
    const animate = (now) => {
      const progress = Math.min(1, (now - start) / 750);
      const eased = 1 - Math.pow(1 - progress, 3);
      node.textContent = `${(target * eased).toFixed(decimals)}${suffix}`;
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
      teal: styles.getPropertyValue("--teal-ink").trim(),
      cyan: styles.getPropertyValue("--cyan-ink").trim(),
      violet: styles.getPropertyValue("--violet-ink").trim(),
      coral: styles.getPropertyValue("--coral-ink").trim(),
    };
  };

  const readData = (id) => {
    const node = document.getElementById(id);
    if (!node) return [];
    try { return JSON.parse(node.textContent); } catch (_) { return []; }
  };

  const chartStates = new WeakMap();
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
    ctx.font = '10px "Segoe UI Variable Text", "Segoe UI", sans-serif';
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
    const pointX = (index) => {
      if (kind === "bar") {
        const slot = chartW / data.length;
        return pad.left + index * slot + slot / 2;
      }
      const step = data.length > 1 ? chartW / (data.length - 1) : chartW;
      return pad.left + (data.length === 1 ? chartW / 2 : index * step);
    };
    chartStates.set(canvas, {
      points: data.map((item, index) => ({
        x: pointX(index),
        y: y(Number(item.value)),
        item,
      })),
    });
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

  const chartUnit = (canvas) => {
    if (canvas.id === "weekly-chart") return "km";
    if (canvas.id === "pace-chart") return "min/km";
    if (canvas.id === "walk-chart") return "walk breaks";
    return "";
  };
  document.querySelectorAll("canvas.chart").forEach((canvas) => {
    const wrap = canvas.closest(".chart-wrap");
    if (!wrap) return;
    const tooltip = document.createElement("div");
    tooltip.className = "chart-tooltip";
    wrap.appendChild(tooltip);
    const show = (event) => {
      const state = chartStates.get(canvas);
      if (!state || !state.points.length) return;
      const rect = canvas.getBoundingClientRect();
      const pointerX = event.clientX - rect.left;
      const nearest = state.points.reduce((best, point) =>
        Math.abs(point.x - pointerX) < Math.abs(best.x - pointerX) ? point : best
      );
      const value = Number(nearest.item.value);
      const shown = Number.isInteger(value) ? value : value.toFixed(2);
      tooltip.textContent = `${nearest.item.label}: ${shown} ${chartUnit(canvas)}`.trim();
      tooltip.style.left = `${Math.max(46, Math.min(rect.width - 46, nearest.x))}px`;
      tooltip.style.top = `${Math.max(30, nearest.y)}px`;
      tooltip.classList.add("visible");
    };
    canvas.addEventListener("pointermove", show);
    canvas.addEventListener("pointerdown", show);
    canvas.addEventListener("pointerleave", (event) => {
      if (event.pointerType !== "touch") tooltip.classList.remove("visible");
    });
    document.addEventListener("pointerdown", (event) => {
      if (!wrap.contains(event.target)) tooltip.classList.remove("visible");
    });
  });

  const weekTabs = [...document.querySelectorAll("[data-week-tab]")];
  const activateWeek = (button) => {
    const week = button.dataset.weekTab;
    weekTabs.forEach((item) => {
      const selected = item === button;
      item.classList.toggle("active", selected);
      item.setAttribute("aria-selected", String(selected));
      item.tabIndex = selected ? 0 : -1;
    });
    document.querySelectorAll("[data-week-panel]").forEach((panel) => {
      const selected = panel.dataset.weekPanel === week;
      panel.classList.toggle("active", selected);
      panel.hidden = !selected;
    });
  };
  weekTabs.forEach((button, index) => {
    button.addEventListener("click", () => activateWeek(button));
    button.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      let next = index;
      if (event.key === "ArrowLeft") next = (index - 1 + weekTabs.length) % weekTabs.length;
      if (event.key === "ArrowRight") next = (index + 1) % weekTabs.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = weekTabs.length - 1;
      activateWeek(weekTabs[next]);
      weekTabs[next].focus();
    });
  });

  document.querySelectorAll("[data-loading-form]").forEach((form) => {
    form.addEventListener("submit", () => {
      const overlay = document.querySelector(".loading-overlay");
      if (overlay) overlay.classList.add("active");
      const button = form.querySelector('button[type="submit"], button:not([type])');
      if (button) { button.disabled = true; button.textContent = button.dataset.loadingText || "Working…"; }
    });
  });

  document.querySelectorAll("[data-dropzone]").forEach((dropzone) => {
    const fileInput = dropzone.querySelector("[data-file-input]");
    const fileLabel = dropzone.querySelector("[data-file-label]");
    if (!fileInput) return;
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
  });

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
  }
})();
