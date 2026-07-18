// Shared broadcast renderer — the ONE place the visual language lives. Every replay shot
// imports this + the broadcast_style_v1 config and changes ONLY camera transform,
// trajectory geometry, and (later) animation timing. Nothing else may diverge per shot.

export async function loadStyle(base = "./broadcast_style_v1") {
  const names = ["color_palette", "camera", "lighting", "materials", "tube_shader", "cards", "stumps"];
  const parts = await Promise.all(names.map(n => fetch(`${base}/${n}.json`).then(r => r.json())));
  const s = {}; names.forEach((n, i) => (s[n] = parts[i]));
  return s;
}

const rnd = () => Math.random();
function mkc(w, h) { const c = document.createElement("canvas"); c.width = w; c.height = h; return [c, c.getContext("2d")]; }
function noise(x, w, h, n, a) { for (let i = 0; i < n; i++) { const d = rnd() < 0.5 ? 0 : 255; x.fillStyle = `rgba(${d},${d},${d},${rnd() * a})`; x.fillRect(rnd() * w, rnd() * h, 2, 2); } }

export function makeCamera(THREE, style) {
  const c = style.camera;
  return new THREE.PerspectiveCamera(c.fov, c.aspect[0] / c.aspect[1], c.near, c.far);
}

export function configureRenderer(THREE, r, style) {
  r.setSize(1920, 1080, false); r.outputColorSpace = THREE.SRGBColorSpace;
  r.toneMapping = THREE.ACESFilmicToneMapping; r.toneMappingExposure = style.camera.toneMappingExposure;
  // NO real-time shadows. The reference's dark on-pitch path is a deliberate broadcast OVERLAY
  // (drawn by makeGroundTrail), NOT a physical cast shadow. Enabling shadows produced grey
  // diagonals across the pitch that the broadcast replay never has. Reproduce what's visible.
  r.shadowMap.enabled = false;
}

// Everything shot-independent: sky, lights, grass, pitch, creases, stumps.
export function buildBaseline(THREE, sc, style) {
  const P = style.color_palette, L = style.lighting, M = style.materials, S = style.stumps;
  sc.background = new THREE.Color(P.background);
  sc.add(new THREE.HemisphereLight(L.hemisphere.sky, L.hemisphere.ground, L.hemisphere.intensity));
  sc.add(new THREE.AmbientLight(0xffffff, L.ambient));
  const sun = new THREE.DirectionalLight(L.sun.color, L.sun.intensity); sun.position.set(...L.sun.position);
  sc.add(sun);   // lights the scene for FORM only (shape on stumps/tube) — casts NO shadow.

  const [gc, gx] = mkc(512, 512);
  for (let i = 0; i < M.grass.stripeBands; i++) { gx.fillStyle = i % 2 ? P.grassStripeA : P.grassStripeB; gx.fillRect(0, i * (512 / M.grass.stripeBands), 512, 512 / M.grass.stripeBands); }
  noise(gx, 512, 512, 30000, 0.08);
  const gtex = new THREE.CanvasTexture(gc); gtex.wrapS = gtex.wrapT = THREE.RepeatWrapping; gtex.repeat.set(M.grass.textureRepeat, M.grass.textureRepeat);
  const grass = new THREE.Mesh(new THREE.PlaneGeometry(400, 400), new THREE.MeshStandardMaterial({ map: gtex, roughness: M.grass.roughness }));
  grass.name = "grass";   // shots may hide it when a reference-baked ground quad replaces it
  grass.rotation.x = -Math.PI / 2; sc.add(grass);

  const [pc, px] = mkc(256, 512); px.fillStyle = P.pitch; px.fillRect(0, 0, 256, 512);
  for (let i = 0; i < 10; i++) { px.fillStyle = `rgba(150,120,80,${0.05 + rnd() * 0.09})`; px.fillRect(0, rnd() * 512, 256, 16 + rnd() * 44); }
  noise(px, 256, 512, 10000, 0.10);
  // Stumps sit at ONE END of the pitch (batsman's crease at z=0); the pitch extends toward
  // the bowler (+Z). This is the true cricket geometry — a wide shot must show the stumps
  // at the far end, not mid-strip.
  const pitch = new THREE.Mesh(new THREE.PlaneGeometry(M.pitch.widthM, M.pitch.lengthM), new THREE.MeshStandardMaterial({ map: new THREE.CanvasTexture(pc), roughness: M.pitch.roughness }));
  pitch.name = "pitch";   // shots may swap the material (e.g. a texture baked from the reference)
  pitch.rotation.x = -Math.PI / 2; pitch.position.set(0, 0.004, M.pitch.lengthM / 2); sc.add(pitch);

  const lineMat = new THREE.MeshBasicMaterial({ color: P.crease });
  // creases: batsman's stump line (z=0), batsman's popping crease (+1.22), bowler's popping (+18.9)
  [0, 1.22, 18.9].forEach(z => { const l = new THREE.Mesh(new THREE.PlaneGeometry(2.64, 0.05), lineMat); l.name = "crease"; l.rotation.x = -Math.PI / 2; l.position.set(0, 0.006, z); sc.add(l); });

  const stumpMat = new THREE.MeshStandardMaterial({ color: P.stump, roughness: S.roughness, metalness: S.metalness });
  const brandMat = new THREE.MeshStandardMaterial({ color: P.stumpBrand, roughness: 0.5 });
  const goldMat = new THREE.MeshStandardMaterial({ color: P.stumpGold, roughness: 0.4, metalness: 0.5 });
  const bailMat = new THREE.MeshStandardMaterial({ color: P.bail, roughness: 0.4 });
  S.positions.forEach(x => {
    const s = new THREE.Mesh(new THREE.CylinderGeometry(S.radius * 0.85, S.radius, S.height, 24), stumpMat); s.position.set(x, S.height / 2, 0); sc.add(s);
    const band = new THREE.Mesh(new THREE.CylinderGeometry(S.radius * 0.93, S.radius * 0.93, S.brandBandH, 24), brandMat); band.position.set(x, S.brandBandY, 0); sc.add(band);
    const ring = new THREE.Mesh(new THREE.CylinderGeometry(S.radius * 0.96, S.radius * 0.96, 0.012, 24), goldMat); ring.position.set(x, S.goldRingY, 0); sc.add(ring);
  });
  const bailR = S.bailR || 0.008;
  [-S.bailOffset, S.bailOffset].forEach(x => { const bl = new THREE.Mesh(new THREE.CylinderGeometry(bailR, bailR, 0.12, 12), bailMat); bl.rotation.z = Math.PI / 2; bl.position.set(x, S.bailY + bailR * 1.5, 0); sc.add(bl); });
}

// The continuous trajectory tube (core + soft glow halo). curve = THREE.Curve.
// `blend` (optional) overrides WHERE the start->end colour transition happens along the tube — this
// is per-shot (where the observed path ends and the prediction begins), NOT part of the frozen style.
// `colors` (optional [start,end]) overrides the tube palette per-shot: some broadcast shots draw the
// path RED->BLUE (f121 close-up), others draw it a uniform PINK/magenta with no blue (f92 wide). The
// colour belongs to the shot's trajectory data, so it's overridable; defaults to the frozen style.
// `colors` accepts EITHER a 2-array of css colors (legacy: start->end across `blend`) OR an array of
// [t, css] stops sampled from the reference frame (extraction-driven: exact per-segment color).
export function makeTube(THREE, sc, style, curve, blend, colors, opts = {}) {
  const T = style.tube_shader, M = style.materials;
  const BL = blend || T.blend;
  const stops = Array.isArray(colors) && colors.length && Array.isArray(colors[0]) ? colors : null;
  const A = new THREE.Color((colors && !stops && colors[0]) || T.colorStart), Bc = new THREE.Color((colors && !stops && colors[1]) || T.colorEnd);
  function colorAt(t) {
    if (!stops) { const f = THREE.MathUtils.smoothstep(t, BL[0], BL[1]); return A.clone().lerp(Bc, f); }
    let i = 0; while (i < stops.length - 2 && stops[i + 1][0] <= t) i++;
    const [t0, c0] = stops[i], [t1, c1] = stops[i + 1];
    const f = Math.min(1, Math.max(0, t1 > t0 ? (t - t0) / (t1 - t0) : 0));
    return new THREE.Color(c0).lerp(new THREE.Color(c1), f);
  }
  const coreR = opts.radius || M.tubeCore.radius;
  const glowR = opts.radius ? opts.radius * 2.17 : M.tubeGlow.radius;
  // opts.radiusAt(t) (optional): variable radius along the tube — the reference tapers toward the
  // release. Built as a custom tube (TubeGeometry only supports a constant radius).
  function buildGeom(radiusFn) {
    const NSEG = T.tubularSegments, NRAD = T.radialSegments;
    const frames = curve.computeFrenetFrames(NSEG, false);
    const pos = [], cols = [], idx = [];
    for (let i = 0; i <= NSEG; i++) {
      const t = i / NSEG, p = curve.getPointAt(t), rad = radiusFn(t);
      const N = frames.normals[Math.min(i, NSEG)], Bv = frames.binormals[Math.min(i, NSEG)];
      const c = colorAt(t);
      for (let j = 0; j <= NRAD; j++) {
        const a = j / NRAD * Math.PI * 2, sin = Math.sin(a), cos = -Math.cos(a);
        pos.push(p.x + rad * (cos * N.x + sin * Bv.x), p.y + rad * (cos * N.y + sin * Bv.y), p.z + rad * (cos * N.z + sin * Bv.z));
        cols.push(c.r, c.g, c.b);
      }
    }
    for (let i = 0; i < NSEG; i++) for (let j = 0; j < NRAD; j++) {
      const a = i * (NRAD + 1) + j;
      idx.push(a, a + NRAD + 1, a + 1, a + 1, a + NRAD + 1, a + NRAD + 2);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute("color", new THREE.Float32BufferAttribute(cols, 3));
    g.setIndex(idx); g.computeVertexNormals();
    return g;
  }
  function build(radius, opacity, clear, rough, scale) {
    const fn = opts.radiusAt ? (t) => Math.max(0.004, opts.radiusAt(t) * (scale || 1)) : () => radius;
    // opts.unlit: reproduce the sampled reference colors EXACTLY (the broadcast tube is a flat
    // graphic with barely any shading); default stays the lit physical material.
    const mat = opts.unlit
      ? new THREE.MeshBasicMaterial({ vertexColors: true, transparent: true, opacity, depthWrite: false, side: THREE.DoubleSide, toneMapped: false })
      : new THREE.MeshPhysicalMaterial({ vertexColors: true, roughness: rough, clearcoat: clear, clearcoatRoughness: 0.85, transparent: true, opacity, depthWrite: false, side: THREE.DoubleSide });
    return new THREE.Mesh(buildGeom(fn), mat);
  }
  // opts.softEdge: broadcast-style tube — matte, uniform color, silhouette FEATHERS to
  // transparent (fresnel alpha falloff). No glow shell: a hard-edged low-alpha shell reads
  // as an outline stripe, which the reference never shows; post bloom supplies the halo.
  if (opts.softEdge) {
    const mat = new THREE.ShaderMaterial({
      uniforms: { uOpacity: { value: opts.softOpacity || 0.82 }, uFeather: { value: opts.feather || 0.55 } },
      vertexShader: `varying vec3 vC; varying vec3 vN; varying vec3 vV;
        void main(){ vC = color; vec4 mv = modelViewMatrix * vec4(position,1.0);
          vN = normalMatrix * normal; vV = -mv.xyz; gl_Position = projectionMatrix * mv; }`,
      fragmentShader: `varying vec3 vC; varying vec3 vN; varying vec3 vV;
        uniform float uOpacity; uniform float uFeather;
        void main(){ float f = abs(dot(normalize(vN), normalize(vV)));
          float a = uOpacity * smoothstep(0.0, uFeather, f);
          gl_FragColor = vec4(pow(vC, vec3(1.0/2.2)), a); }`,   // linear->approx sRGB (unlit path)
      transparent: true, depthWrite: false, side: THREE.DoubleSide, vertexColors: true,
    });
    const fn = opts.radiusAt ? (t) => Math.max(0.004, opts.radiusAt(t) * 1.12) : () => coreR * 1.12;
    sc.add(new THREE.Mesh(buildGeom(fn), mat));
    return;
  }
  const glowScale = opts.glowScale || 2.17;
  sc.add(build(coreR, M.tubeCore.opacity, M.tubeCore.clearcoat, M.tubeCore.roughness, 1));  // NO castShadow: the trajectory is a graphic, not a shadow-caster.
  sc.add(build(glowR, M.tubeGlow.opacity, 0.0, 1.0, glowScale));
}

// Dark ground trail = the ball's "line & length" band drawn ON the pitch. This is a deliberate
// broadcast OVERLAY, NOT a cast shadow: a flat ribbon following the ball's ground line, darkening
// the pitch (~x0.6, measured from the reference) with SOFT faded edges. Built as a real ground-plane
// ribbon (perpendicular offsets along the path) with a cross-width alpha gradient so the edges fade.
export function makeGroundTrail(THREE, sc, curve, opts = {}) {
  // EXTRACTION-DRIVEN mode: opts.rows = [{L:Vector3, R:Vector3, ratio}] — exact band edges unprojected
  // from the reference. A BLACK overlay at alpha (1-ratio) darkens the pitch to exactly dst*ratio
  // (same math as a multiply, on the reliable normal-blending path). Alpha texture: U = soft cross
  // fade, V = per-row measured strength.
  if (opts.rows) {
    const rows = opts.rows, pos = [], uv = [], idx = [];
    const XS = [0, 0.18, 0.5, 0.82, 1];                    // cross-section: edge->core->edge
    rows.forEach((r, i) => {
      const v = i / (rows.length - 1);
      XS.forEach((s, j) => {
        const p = r.L.clone().lerp(r.R, s);
        pos.push(p.x, opts.y || 0.011, p.z);
        uv.push(s, v);
      });
      if (i < rows.length - 1) {
        const k = i * XS.length;
        for (let j = 0; j < XS.length - 1; j++) idx.push(k + j, k + j + 1, k + j + XS.length, k + j + 1, k + j + XS.length + 1, k + j + XS.length);
      }
    });
    const TW = 64, TH = 256;
    const [cv, cx] = mkc(TW, TH);
    const img = cx.createImageData(TW, TH);
    for (let ty = 0; ty < TH; ty++) {
      const rv = ty / (TH - 1) * (rows.length - 1);
      const r0 = rows[Math.floor(rv)], r1 = rows[Math.min(rows.length - 1, Math.floor(rv) + 1)];
      const f = rv - Math.floor(rv);
      const ratio = r0.ratio + (r1.ratio - r0.ratio) * f;
      for (let tx = 0; tx < TW; tx++) {
        const s = tx / (TW - 1);
        const cross = Math.min(1, Math.min(s, 1 - s) / 0.18);   // fade the outer 18%
        const a = Math.round((1 - ratio) * cross * 255);
        const o = (ty * TW + tx) * 4;
        img.data[o] = 0; img.data[o + 1] = 0; img.data[o + 2] = 0; img.data[o + 3] = a;
      }
    }
    cx.putImageData(img, 0, 0);
    const tex = new THREE.CanvasTexture(cv);
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
    g.setIndex(idx);
    sc.add(new THREE.Mesh(g, new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false, side: THREE.DoubleSide })));
    return;
  }
  const N = opts.samples || 170, halfW = (opts.width || 0.42) / 2, yLift = opts.y || 0.011;
  const opacity = opts.opacity || 0.42, color = opts.color || 0x0a0806;
  const pts = curve.getPoints(N);
  // cross-width soft alpha profile: transparent edge -> opaque centre -> transparent edge
  const [cv, cx] = mkc(64, 4);
  const grad = cx.createLinearGradient(0, 0, 64, 0);
  grad.addColorStop(0.00, "rgba(0,0,0,0)"); grad.addColorStop(0.18, "rgba(0,0,0,0.55)");
  grad.addColorStop(0.50, "rgba(0,0,0,1)"); grad.addColorStop(0.82, "rgba(0,0,0,0.55)");
  grad.addColorStop(1.00, "rgba(0,0,0,0)");
  cx.fillStyle = grad; cx.fillRect(0, 0, 64, 4);
  const tex = new THREE.CanvasTexture(cv);
  const up = new THREE.Vector3(0, 1, 0), pos = [], uv = [], idx = [];
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i], a = pts[Math.max(0, i - 1)], b = pts[Math.min(pts.length - 1, i + 1)];
    const tan = new THREE.Vector3(b.x - a.x, 0, b.z - a.z).normalize();
    const lat = new THREE.Vector3().crossVectors(tan, up).normalize();  // perpendicular, on the ground
    pos.push(p.x - lat.x * halfW, yLift, p.z - lat.z * halfW, p.x + lat.x * halfW, yLift, p.z + lat.z * halfW);
    const v = i / (pts.length - 1); uv.push(0, v, 1, v);
    if (i < pts.length - 1) { const k = i * 2; idx.push(k, k + 1, k + 2, k + 1, k + 3, k + 2); }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
  g.setIndex(idx);
  sc.add(new THREE.Mesh(g, new THREE.MeshBasicMaterial({ map: tex, color, transparent: true, opacity, depthWrite: false })));
}

// `override` (optional {color, emissive}) sets the ball colour per-shot (f92's balls read pinker
// than the frozen palette); defaults to the shared palette keys.
export function makeBall(THREE, sc, style, pos, colorKey, emissiveKey, override) {
  const P = style.color_palette, M = style.materials;
  const color = (override && override.color) || P[colorKey], emissive = (override && override.emissive) || P[emissiveKey];
  const m = new THREE.Mesh(new THREE.SphereGeometry(M.ball.radius, 28, 28), new THREE.MeshPhysicalMaterial({ color, roughness: M.ball.roughness, clearcoat: M.ball.clearcoat, emissive, emissiveIntensity: 0.45 }));
  m.position.copy(pos); sc.add(m);
}

// Glossy HUD cards from cards.json (injected once, shared style).
// `layout` (optional) = extraction-driven mode: [{label,status,tone,x,y,w,hHdr,hStat}] boxes measured
// from the reference frame — each card absolutely positioned; the shared visual style still applies.
export function buildCards(mount, style, layout) {
  const P0 = style.color_palette;
  const grad0 = (a) => `linear-gradient(${a[0]},${a[1]})`;
  if (layout) {
    layout.forEach(c => {
      // measured gradients from the reference frame when present; palette fallback otherwise
      const hg = c.hdrTop ? `linear-gradient(${c.hdrTop},${c.hdrBot})`
                          : `linear-gradient(${P0.cardHeaderTop},${P0.cardHeaderBottom} 55%,#0a4d82)`;
      const sg = c.statTop ? `linear-gradient(${c.statTop},${c.statBot})`
                           : grad0(c.tone === "green" ? P0.cardGreen : P0.cardRed);
      const el = document.createElement("div");
      el.style.cssText = `position:absolute;left:${c.x}px;top:${c.y}px;width:${c.w}px;` +
        `font-family:Arial,Helvetica,sans-serif;font-weight:700;border:2px solid #cfe0ef;` +
        `border-radius:3px;overflow:hidden;box-shadow:0 4px 14px #0007;box-sizing:border-box`;
      el.innerHTML =
        `<div style="background:${hg};color:#fff;text-align:center;height:${c.hHdr}px;line-height:${c.hHdr}px;font-size:${Math.round(c.hHdr * 0.50)}px;text-shadow:0 1px 2px #0008">${c.label}</div>` +
        `<div style="background:${sg};color:#fff;text-align:center;height:${c.hStat}px;line-height:${c.hStat}px;font-size:${Math.round(c.hStat * 0.50)}px;text-shadow:0 1px 2px #0006">${c.status}</div>`;
      mount.appendChild(el);
    });
    return;
  }
  const C = style.cards, P = style.color_palette;
  const grad = (a) => `linear-gradient(${a[0]},${a[1]})`;
  const css = `.bcards{position:absolute;left:${C.left}px;top:${C.top}px;width:${C.width}px;font-family:Arial,Helvetica,sans-serif;font-weight:800}
  .bcard{margin:14px 0;border:2px solid #cfe0ef;border-radius:3px;overflow:hidden;box-shadow:0 4px 14px #0007}
  .bhdr{background:linear-gradient(${P.cardHeaderTop},${P.cardHeaderBottom} 55%,#0a4d82);color:#fff;text-align:center;font-size:30px;padding:8px 6px;text-shadow:0 1px 2px #0008}
  .bstat{color:#fff;text-align:center;font-size:28px;padding:7px 6px;text-shadow:0 1px 2px #0006}`;
  const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
  const wrap = document.createElement("div"); wrap.className = "bcards";
  C.cards.forEach(c => {
    if (c.gap) { const g = document.createElement("div"); g.style.height = c.gap + "px"; wrap.appendChild(g); return; }
    const el = document.createElement("div"); el.className = "bcard";
    el.innerHTML = `<div class="bhdr">${c.label}</div><div class="bstat" style="background:${grad(c.tone === "green" ? P.cardGreen : P.cardRed)}">${c.status}</div>`;
    wrap.appendChild(el);
  });
  mount.appendChild(wrap);
}
