(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const el = {
    source: $("sourceLang"), target: $("targetLang"), input: $("inputText"),
    counter: $("counter"), translate: $("translateBtn"), translateLabel: $("translateLabel"),
    spinner: $("spinner"), swap: $("swapBtn"), clear: $("clearBtn"), copy: $("copyBtn"),
    copyLabel: $("copyLabel"), output: $("output"), placeholder: $("placeholder"),
    skeleton: $("skeleton"), result: $("resultText"), meta: $("resultMeta"),
    detected: $("detectedBadge"), banner: $("banner"), bannerText: $("bannerText"),
    bannerClose: $("bannerClose"), provider: $("providerBadge"), heroWord: $("heroWord"),
  };

  const MAX = (window.LINGUA && window.LINGUA.maxChars) || 5000;
  const RTL = new Set(["AR", "HE", "FA", "UR", "PS", "YI", "CKB", "PRS"]);
  const state = { languages: [], detection: false, busy: false, lastDetected: null, lastResult: "", controller: null };

  const base = (code) => String(code || "").split("-")[0].toUpperCase();
  const same = (a, b) => String(a).toUpperCase() === String(b).toUpperCase();

  /* ---------------- messages ---------------- */
  function showBanner(message, kind = "error") {
    el.bannerText.textContent = message;
    el.banner.classList.toggle("info", kind === "info");
    el.banner.hidden = false;
  }
  function hideBanner() { el.banner.hidden = true; }
  el.bannerClose.addEventListener("click", hideBanner);

  /* ---------------- UI state ---------------- */
  function setBusy(busy) {
    state.busy = busy;
    el.spinner.hidden = !busy;
    el.translateLabel.textContent = busy ? "Translating…" : "Translate";
    el.skeleton.hidden = !busy;
    if (busy) { el.placeholder.hidden = true; el.result.hidden = true; }
    refreshButtons();
  }

  function refreshButtons() {
    const ready = state.languages.length > 0;
    el.translate.disabled = state.busy || !ready || !el.input.value.trim();
    el.swap.disabled = state.busy || !ready;
    el.copy.disabled = !state.lastResult;
    el.clear.disabled = state.busy;
  }

  function updateCounter() {
    const n = el.input.value.length;
    el.counter.textContent = `${n.toLocaleString()} / ${MAX.toLocaleString()}`;
    el.counter.classList.toggle("warn", n > MAX * 0.9 && n < MAX);
    el.counter.classList.toggle("max", n >= MAX);
    refreshButtons();
  }

  function resetOutput() {
    state.lastResult = "";
    el.result.hidden = true; el.result.textContent = "";
    el.skeleton.hidden = true; el.placeholder.hidden = false;
    el.meta.textContent = ""; el.detected.hidden = true;
    state.lastDetected = null;
    refreshButtons();
  }

  function showResult(text) {
    state.lastResult = text;
    el.result.textContent = text;
    el.result.hidden = false; el.placeholder.hidden = true; el.skeleton.hidden = true;
    el.result.classList.remove("fade-in"); void el.result.offsetWidth; el.result.classList.add("fade-in");
    el.output.setAttribute("dir", RTL.has(base(el.target.value)) ? "rtl" : "ltr");
    refreshButtons();
  }

  /* ---------------- languages ---------------- */
  function fillSelect(select, items, selected, extraFirst) {
    select.innerHTML = "";
    if (extraFirst) select.appendChild(new Option(extraFirst.label, extraFirst.value));
    items.forEach((l) => select.appendChild(new Option(l.name, l.code)));
    if (selected && [...select.options].some((o) => same(o.value, selected))) {
      select.value = [...select.options].find((o) => same(o.value, selected)).value;
    }
    select.disabled = false;
  }

  function pick(preferred, options) {
    return preferred.find((p) => options.some((o) => same(o.code, p)));
  }

  async function loadLanguages() {
    try {
      const res = await fetch("/api/languages");
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Could not load languages.");
      state.languages = data.languages;
      state.detection = !!data.supports_detection;

      const sources = data.languages.filter((l) => l.source);
      const targets = data.languages.filter((l) => l.target);
      fillSelect(el.source, sources, pick(["EN"], sources),
        state.detection ? { label: "Detect language", value: "auto" } : null);
      if (state.detection) el.source.value = "auto";
      fillSelect(el.target, targets, pick(["ES", "FR", "DE"], targets));

      if (data.provider) { el.provider.textContent = `Powered by ${data.provider}`; el.provider.hidden = false; }
      updateCounter();
    } catch (err) {
      el.source.innerHTML = el.target.innerHTML = "<option>Unavailable</option>";
      showBanner(err.message || "Could not load languages. Is the server configured?");
    }
  }

  /* ---------------- translate ---------------- */
  async function translate() {
    hideBanner();
    const text = el.input.value.trim();
    if (!text) { showBanner("Please enter some text to translate.", "info"); el.input.focus(); return; }
    if (text.length > MAX) { showBanner(`Text is too long. The limit is ${MAX.toLocaleString()} characters.`); return; }
    if (!el.target.value) { showBanner("Please choose a target language."); return; }
    if (el.source.value !== "auto" && same(el.source.value, el.target.value)) {
      showBanner("Source and target languages are the same. Please pick two different languages.", "info"); return;
    }

    if (state.controller) state.controller.abort();
    const controller = new AbortController();
    state.controller = controller;
    const timer = setTimeout(() => controller.abort("timeout"), 45000);

    resetOutput();
    setBusy(true);
    try {
      const res = await fetch("/api/translate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, source: el.source.value, target: el.target.value }),
        signal: controller.signal,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `Request failed (HTTP ${res.status}).`);

      showResult(data.translation);
      if (data.detected_source) {
        state.lastDetected = data.detected_source;
        const match = state.languages.find((l) => l.source && same(l.code, data.detected_source))
          || state.languages.find((l) => same(base(l.code), base(data.detected_source)));
        if (el.source.value === "auto") {
          el.detected.textContent = `Detected: ${match ? match.name : data.detected_source}`;
          el.detected.hidden = false;
        }
      }
      el.meta.textContent = `${data.characters.toLocaleString()} characters · ${data.provider}`;
    } catch (err) {
      resetOutput();
      if (err.name === "AbortError" || err === "timeout") {
        if (controller.signal.reason === "timeout") showBanner("The request took too long. Please try again.");
      } else if (err instanceof TypeError) {
        showBanner("Can't reach the server. Is it still running?");
      } else {
        showBanner(err.message);
      }
    } finally {
      clearTimeout(timer);
      if (state.controller === controller) { state.controller = null; setBusy(false); }
    }
  }

  /* ---------------- swap ---------------- */
  function swapLanguages() {
    hideBanner();
    const srcVal = el.source.value === "auto" ? (state.lastDetected || null) : el.source.value;
    if (!srcVal) {
      showBanner("Translate something first so the source language is known, or choose a source language to swap.", "info");
      return;
    }
    const tgtVal = el.target.value;

    // New target = old source (prefer exact code, otherwise a regional variant such as EN -> EN-US).
    const targets = state.languages.filter((l) => l.target);
    const newTarget = targets.find((l) => same(l.code, srcVal))
      || targets.find((l) => base(l.code) === base(srcVal));
    // New source = old target (prefer exact code, otherwise its base language such as EN-US -> EN).
    const sources = state.languages.filter((l) => l.source);
    const newSource = sources.find((l) => same(l.code, tgtVal))
      || sources.find((l) => same(l.code, base(tgtVal)))
      || sources.find((l) => base(l.code) === base(tgtVal));

    if (!newTarget || !newSource) {
      showBanner("Those two languages can't be swapped with the current provider.", "info");
      return;
    }
    el.source.value = newSource.code;
    el.target.value = newTarget.code;
    el.detected.hidden = true;

    // Move the translation into the input box so the swap feels natural.
    if (state.lastResult) {
      el.input.value = state.lastResult.slice(0, MAX);
      resetOutput();
      updateCounter();
    }
  }

  /* ---------------- copy / clear ---------------- */
  async function copyResult() {
    if (!state.lastResult) return;
    let ok = false;
    try {
      await navigator.clipboard.writeText(state.lastResult);
      ok = true;
    } catch (_) {
      // Fallback for non-secure contexts / older browsers.
      const ta = document.createElement("textarea");
      ta.value = state.lastResult; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { ok = document.execCommand("copy"); } catch (_) { ok = false; }
      document.body.removeChild(ta);
    }
    if (ok) {
      el.copy.classList.add("copied"); el.copyLabel.textContent = "Copied!";
      setTimeout(() => { el.copy.classList.remove("copied"); el.copyLabel.textContent = "Copy"; }, 1600);
    } else {
      showBanner("Couldn't copy automatically. Select the text and copy it manually.");
    }
  }

  function clearAll() {
    if (state.controller) state.controller.abort();
    el.input.value = "";
    hideBanner(); resetOutput(); updateCounter(); el.input.focus();
  }

  /* ---------------- events ---------------- */
  el.translate.addEventListener("click", translate);
  el.swap.addEventListener("click", swapLanguages);
  el.copy.addEventListener("click", copyResult);
  el.clear.addEventListener("click", clearAll);
  el.input.addEventListener("input", updateCounter);
  el.input.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); if (!el.translate.disabled) translate(); }
  });
  el.source.addEventListener("change", () => { el.detected.hidden = true; });

  // Rotating hero word (purely decorative).
  const words = ["any language", "your language", "every language", "their language"];
  let wi = 0;
  setInterval(() => {
    el.heroWord.style.opacity = 0;
    setTimeout(() => { wi = (wi + 1) % words.length; el.heroWord.textContent = words[wi]; el.heroWord.style.opacity = 1; }, 220);
  }, 3500);

  updateCounter();
  loadLanguages();
})();
