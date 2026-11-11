/* MassLens 3D 视图：three.js 渲染索网振型动画。
   振型位移在 GPU 顶点着色器中完成（position + shape·amp·sin(相位)），
   幅值色带用 viridis LUT 纹理，膜面与索共用同一份位移属性。
   阶次/参数切换用双缓冲 + uMix 在着色器内交叉淡化：
   连续拖动时呈指数追赶（平滑不拖尾），单次切换约 0.28 s 过渡完毕。 */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

var TRANSITION_SEC = 0.28;

var VIRIDIS_STOPS = ["#440154", "#482677", "#3f4788", "#31688e", "#1fa187",
                     "#4ac16d", "#a0da39", "#d8e219", "#fde725"];

function hexToRgb(hex) {
  return [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16),
          parseInt(hex.slice(5, 7), 16)];
}

function buildViridisLUT() {
  var n = 256;
  var data = new Uint8Array(n * 4);
  var stops = VIRIDIS_STOPS.map(hexToRgb);
  for (var i = 0; i < n; i++) {
    var t = (i / (n - 1)) * (stops.length - 1);
    var k = Math.min(Math.floor(t), stops.length - 2);
    var f = t - k;
    for (var c = 0; c < 3; c++) {
      data[i * 4 + c] = Math.round(stops[k][c] * (1 - f) + stops[k + 1][c] * f);
    }
    data[i * 4 + 3] = 255;
  }
  var tex = new THREE.DataTexture(data, n, 1, THREE.RGBAFormat);
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.wrapS = THREE.ClampToEdgeWrapping;
  tex.wrapT = THREE.ClampToEdgeWrapping;
  tex.generateMipmaps = false;
  tex.needsUpdate = true;
  return tex;
}

var DIVERGE_STOPS = ["#38bdf8", "#141c2e", "#f59e0b"];  // 负脉动青 → 零深底 → 正脉动琥珀

function buildDivergeLUT() {
  var n = 256;
  var data = new Uint8Array(n * 4);
  var stops = DIVERGE_STOPS.map(hexToRgb);
  for (var i = 0; i < n; i++) {
    var t = (i / (n - 1)) * (stops.length - 1);
    var k = Math.min(Math.floor(t), stops.length - 2);
    var f = t - k;
    for (var c = 0; c < 3; c++) {
      data[i * 4 + c] = Math.round(stops[k][c] * (1 - f) + stops[k + 1][c] * f);
    }
    data[i * 4 + 3] = 255;
  }
  var tex = new THREE.DataTexture(data, n, 1, THREE.RGBAFormat);
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.wrapS = THREE.ClampToEdgeWrapping;
  tex.wrapT = THREE.ClampToEdgeWrapping;
  tex.generateMipmaps = false;
  tex.needsUpdate = true;
  return tex;
}

var VERT = [
  "uniform float uAmp;",
  "uniform float uPhase;",
  "uniform float uMaxAbs;",
  "uniform float uMix;",
  "attribute vec3 aShapeOld;",
  "attribute vec3 aShapeNew;",
  "attribute float aPress;",
  "varying float vAmp;",
  "varying float vPress;",
  "void main() {",
  "  vec3 shape = mix(aShapeOld, aShapeNew, uMix);",
  "  float s = sin(uPhase);",
  "  vec3 p = position + shape * (uAmp * s);",
  "  vAmp = clamp(length(shape) / uMaxAbs * abs(s), 0.0, 1.0);",
  "  vPress = aPress;",
  "  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);",
  "}"
].join("\n");

var FRAG = [
  "uniform sampler2D uLUT;",
  "uniform sampler2D uLUT2;",
  "uniform vec3 uBase;",
  "uniform float uTint;",
  "uniform float uPressMix;",
  "varying float vAmp;",
  "varying float vPress;",
  "void main() {",
  "  vec3 c = texture2D(uLUT, vec2(vAmp, 0.5)).rgb;",
  "  c = mix(uBase, c, uTint * smoothstep(0.03, 0.30, vAmp));",
  "  vec3 pc = texture2D(uLUT2, vec2(clamp(vPress * 0.5 + 0.5, 0.0, 1.0), 0.5)).rgb;",
  "  c = mix(c, pc, uPressMix * smoothstep(0.02, 0.15, abs(vPress)));",
  "  gl_FragColor = vec4(c, 1.0);",
  "}"
].join("\n");

function makeDispMaterial(lut, lut2, base, tint, side) {
  return new THREE.ShaderMaterial({
    uniforms: {
      uAmp: { value: 0.0 },
      uPhase: { value: 0.0 },
      uMaxAbs: { value: 1.0 },
      uMix: { value: 1.0 },
      uLUT: { value: lut },
      uLUT2: { value: lut2 },
      uBase: { value: new THREE.Vector3(base[0], base[1], base[2]) },
      uTint: { value: tint },
      uPressMix: { value: 0.0 }
    },
    vertexShader: VERT,
    fragmentShader: FRAG,
    side: side
  });
}

export class Viewer3D {
  constructor(container) {
    this.container = container;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(container.clientWidth, container.clientHeight);
    container.appendChild(this.renderer.domElement);

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(
      45, container.clientWidth / Math.max(container.clientHeight, 1), 0.5, 5000);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;

    this.lut = buildViridisLUT();
    this.lut2 = buildDivergeLUT();
    this.membraneMat = makeDispMaterial(this.lut, this.lut2, [0.16, 0.20, 0.30], 1.0,
                                        THREE.DoubleSide);
    this.cableMat = makeDispMaterial(this.lut, this.lut2, [0.30, 0.38, 0.55], 0.6,
                                     THREE.FrontSide);
    this.phase = 0;
    this.speed = 0.6;  // 视觉频率 Hz
    this.radius = 1;
    this.fps = 60;  // EMA 帧率（?debug=1 角标用）

    this.shapeOld = null;  // 膜/索共享的双缓冲位移数组（N*3）
    this.shapeNew = null;
    this.shapeAttrs = [];
    this.pressArr = null;  // 脉动风压插值（每节点标量，[-1,1]）
    this.pressAttrs = [];
    this.meshes = [];
    this.mix = 1;
    this.hasShape = false;
    this.ampFrom = 0; this.ampTo = 0;
    this.maxFrom = 1; this.maxTo = 1;

    var self = this;
    this._last = performance.now();
    function loop(now) {
      var dt = Math.min((now - self._last) / 1000, 0.1);
      self._last = now;
      self.fps = self.fps * 0.95 + (1 / Math.max(dt, 1e-3)) * 0.05;
      self.phase += 2 * Math.PI * self.speed * dt;
      if (self.mix < 1) {  // 过渡进行中：线性推进并同步插值幅值
        self.mix = Math.min(1, self.mix + dt / TRANSITION_SEC);
        var amp = self.ampFrom + (self.ampTo - self.ampFrom) * self.mix;
        var mx = self.maxFrom + (self.maxTo - self.maxFrom) * self.mix;
        self._setUniforms(amp, mx, self.mix);
      }
      self.membraneMat.uniforms.uPhase.value = self.phase;
      self.cableMat.uniforms.uPhase.value = self.phase;
      self.controls.update();
      self.renderer.render(self.scene, self.camera);
      requestAnimationFrame(loop);
    }
    requestAnimationFrame(loop);

    new ResizeObserver(function () {
      var w = container.clientWidth, h = Math.max(container.clientHeight, 1);
      self.camera.aspect = w / h;
      self.camera.updateProjectionMatrix();
      self.renderer.setSize(w, h);
    }).observe(container);
  }

  _setUniforms(amp, maxAbs, mix) {
    var mats = [this.membraneMat, this.cableMat];
    for (var i = 0; i < mats.length; i++) {
      mats[i].uniforms.uAmp.value = amp;
      mats[i].uniforms.uMaxAbs.value = maxAbs;
      mats[i].uniforms.uMix.value = mix;
    }
  }

  setGeometry(coords, tris, lines) {
    var i;
    for (i = 0; i < this.meshes.length; i++) {
      this.scene.remove(this.meshes[i]);
      this.meshes[i].geometry.dispose();
    }
    this.meshes = [];

    var n = coords.length / 3;
    this.shapeOld = new Float32Array(n * 3);
    this.shapeNew = new Float32Array(n * 3);
    this.pressArr = new Float32Array(n);
    var attrOM = new THREE.BufferAttribute(this.shapeOld, 3);
    var attrNM = new THREE.BufferAttribute(this.shapeNew, 3);
    var attrOL = new THREE.BufferAttribute(this.shapeOld, 3);
    var attrNL = new THREE.BufferAttribute(this.shapeNew, 3);
    var attrPM = new THREE.BufferAttribute(this.pressArr, 1);
    var attrPL = new THREE.BufferAttribute(this.pressArr, 1);
    this.shapeAttrs = [attrOM, attrNM, attrOL, attrNL];
    this.pressAttrs = [attrPM, attrPL];
    var geoM = new THREE.BufferGeometry();
    geoM.setAttribute("position", new THREE.BufferAttribute(coords, 3));
    geoM.setAttribute("aShapeOld", attrOM);
    geoM.setAttribute("aShapeNew", attrNM);
    geoM.setAttribute("aPress", attrPM);
    geoM.setIndex(new THREE.BufferAttribute(tris, 1));
    var meshM = new THREE.Mesh(geoM, this.membraneMat);
    this.scene.add(meshM);

    var geoL = new THREE.BufferGeometry();
    geoL.setAttribute("position", new THREE.BufferAttribute(coords, 3));
    geoL.setAttribute("aShapeOld", attrOL);
    geoL.setAttribute("aShapeNew", attrNL);
    geoL.setAttribute("aPress", attrPL);
    geoL.setIndex(new THREE.BufferAttribute(lines, 1));
    var meshL = new THREE.LineSegments(geoL, this.cableMat);
    this.scene.add(meshL);
    this.meshes = [meshM, meshL];

    geoM.computeBoundingSphere();
    var c = geoM.boundingSphere.center;
    this.radius = geoM.boundingSphere.radius;
    this.camera.position.set(c.x + this.radius * 1.05,
                             c.y + this.radius * 0.55,
                             c.z + this.radius * 1.05);
    this.controls.target.copy(c);
    this.camera.near = this.radius / 200;
    this.camera.far = this.radius * 30;
    this.camera.updateProjectionMatrix();
    this.hasShape = false;
  }

  setShape(shape, maxAbs) {
    if (!this.shapeNew || shape.length !== this.shapeNew.length) return;
    var amp = maxAbs > 0 ? (0.035 * this.radius) / maxAbs : 0;  // 幅度 0.06→0.035（用户反馈收敛）
    var ma = maxAbs > 0 ? maxAbs : 1;
    var i;
    if (!this.hasShape) {  // 首次：直接落位，无过渡
      this.shapeOld.set(shape);
      this.shapeNew.set(shape);
      this.mix = 1;
      this.ampFrom = this.ampTo = amp;
      this.maxFrom = this.maxTo = ma;
      this.hasShape = true;
      this._setUniforms(amp, ma, 1);
    } else {
      // 把当前实际显示（old→new 的 mix 处）拷回 old，作为本次过渡起点
      var m = this.mix, old = this.shapeOld, nw = this.shapeNew;
      for (i = 0; i < old.length; i++) old[i] = old[i] + (nw[i] - old[i]) * m;
      nw.set(shape);
      this.ampFrom = this.ampFrom + (this.ampTo - this.ampFrom) * m;
      this.maxFrom = this.maxFrom + (this.maxTo - this.maxFrom) * m;
      this.ampTo = amp;
      this.maxTo = ma;
      this.mix = 0;
      this._setUniforms(this.ampFrom, this.maxFrom, 0);
    }
    for (i = 0; i < this.shapeAttrs.length; i++) {
      this.shapeAttrs[i].needsUpdate = true;
    }
  }

  setSpeed(hz) { this.speed = hz; }

  setShapeImmediate(shape, maxAbs) {
    /* 每帧直写（WindLens 频域合成用）：跳过交叉淡化，uMix 恒 1。
       约定 shape 已是 0.06·radius 量级的位移场，maxAbs 为其最大幅值。 */
    if (!this.shapeNew || shape.length !== this.shapeNew.length) return;
    this.shapeNew.set(shape);
    var amp = maxAbs > 0 ? (0.035 * this.radius) / maxAbs : 0;
    var ma = maxAbs > 0 ? maxAbs : 1;
    this.ampFrom = this.ampTo = amp;
    this.maxFrom = this.maxTo = ma;
    this.mix = 1;
    this.hasShape = true;
    this._setUniforms(amp, ma, 1);
    for (var i = 0; i < this.shapeAttrs.length; i++) {
      this.shapeAttrs[i].needsUpdate = true;
    }
  }

  setPress(arr) {
    /* 脉动风压插值写入（每节点标量，[-1,1]，负=吸、正=压）。 */
    if (!this.pressArr || arr.length !== this.pressArr.length) return;
    this.pressArr.set(arr);
    for (var i = 0; i < this.pressAttrs.length; i++) {
      this.pressAttrs[i].needsUpdate = true;
    }
  }

  setPressMix(v) {
    /* 0=纯振型色 / 1=风压云图主导（叠加模式取中间值）。 */
    this.membraneMat.uniforms.uPressMix.value = v;
    this.cableMat.uniforms.uPressMix.value = v;
  }
}
