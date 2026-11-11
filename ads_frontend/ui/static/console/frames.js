// 구성별로 보이는 모습(설계 §10 "구성 표시 정보", SDI-1). 보기만 한다 — 없어도 모든 흐름이 동작하고, 서버는 비율을 검사하지 않는다.
// 구성마다 매체가 그리는 방식대로(늘림·잘림·이미지 전체) 같은 이미지를 그리고, 비율이 다르면 얼마나 눌리거나 잘리는지 적는다.
(() => {
  const sections = [...document.querySelectorAll("[data-frames]")];
  if (!sections.length) return;
  const pct = (x) => Math.round(x * 100);

  // 이미지(iw×ih)를 판(w:h)에 그 방식(fit)으로 그렸을 때의 차이. 비율 차이 2% 미만은 맞는 것으로 본다
  const note = (fit, iw, ih, w, h) => {
    if (!w) return "원본 비율 그대로 보입니다(크기는 화면에 따라 다릅니다).";
    const img = iw / ih, box = w / h;
    if (Math.abs(img / box - 1) < 0.02) return "비율이 맞습니다.";
    const wide = img > box, loss = pct(wide ? 1 - box / img : 1 - img / box);
    if (fit === "stretch") return `비율이 달라 ${wide ? "가로" : "세로"}가 ${loss}% 눌려 보입니다.`;
    if (fit === "cover") return `비율이 달라 ${wide ? "좌우" : "위아래"}가 합쳐 ${loss}% 잘립니다.`;
    return `비율이 달라 ${wide ? "위아래" : "좌우"}에 빈 곳이 생깁니다.`;
  };

  // 체크박스로 고른 구성만 보일 때(광고 만들기): 고르지 않은 구성은 숨긴다
  const refresh = (section) => {
    const pick = section.dataset.pick;
    const chosen = pick ? new Set([...document.querySelectorAll(`input[name="${pick}"]:checked`)].map((c) => c.value)) : null;
    let shown = 0;
    section.querySelectorAll("figure.frame").forEach((figure) => {
      figure.hidden = chosen !== null && !chosen.has(figure.dataset.slot);
      if (!figure.hidden) shown += 1;
    });
    section.querySelector("[data-frames-empty]").hidden = shown > 0 && Boolean(section.dataset.drawn);
  };

  const draw = (section, src) => {
    section.dataset.drawn = src || "";
    section.querySelectorAll("figure.frame").forEach((figure) => {
      const box = figure.querySelector(".frame-box"), out = figure.querySelector(".frame-note");
      const { fit, width, height } = figure.dataset;
      if (width) box.style.aspectRatio = `${width} / ${height}`;  // CSSOM 이라 CSP 가 막는 style 속성이 아니다
      let img = box.querySelector("img");
      if (!img) {
        img = document.createElement("img");
        img.alt = `${figure.querySelector("figcaption strong").textContent}에서 보이는 모습`;
        box.append(img);
      }
      img.hidden = true;
      out.textContent = "";
      if (!fit || !src) return;  // 표시 정보가 없는 구성은 이름과 안내만
      img.onload = () => { img.hidden = false; out.textContent = note(fit, img.naturalWidth, img.naturalHeight, Number(width), Number(height)); };
      img.onerror = () => { img.hidden = true; };  // 그림이 아닌 파일(서버가 거절 사유를 알린다)
      img.src = src;
    });
    refresh(section);
  };

  sections.forEach((section) => {
    const input = section.dataset.input ? document.getElementById(section.dataset.input) : null;
    if (input) {
      input.addEventListener("change", () => {
        const file = input.files[0];
        if (!file) { draw(section, section.dataset.source); return; }
        const reader = new FileReader();
        reader.onload = () => draw(section, reader.result);
        reader.readAsDataURL(file);
      });
    }
    if (section.dataset.pick) document.addEventListener("change", (e) => { if (e.target.name === section.dataset.pick) refresh(section); });
    draw(section, section.dataset.source);
  });

  // 광고 만들기가 고른 소재(등록된 소재·새로 올리기)가 바뀔 때 부른다(ad_create.js). 같은 이미지면 다시 그리지 않는다
  window.ConsoleFrames = { draw: (src) => sections.forEach((s) => { if (s.dataset.drawn !== (src || "")) draw(s, src); }) };
})();
