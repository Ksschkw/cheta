/* Cheta 3D hero. Vanilla JS, no build step, no CDN, no external assets.
   ASCII only.

   The scene is the product idea rather than decoration: a dark field of small
   monochrome nodes. Nodes appear as facts are stored and connect to their
   nearest neighbours. A share of those links dim and detach over time, which
   is consolidation: repeated facts collapsing and changed minds retiring. A
   handful of links stay and carry the single accent. The scene runs once and
   freezes in a still, readable frame so it can be screen-recorded.

   With prefers-reduced-motion the scene is rendered once in its settled state. */

(function (global) {
  "use strict";

  var BG = 0x0a0a0a;
  var ACCENT = 0xd4a853;
  var NODE = 0x8a8a8a;
  var LINE = 0x262626;

  function prefersReducedMotion() {
    return !!(
      global.matchMedia &&
      global.matchMedia("(prefers-reduced-motion: reduce)").matches
    );
  }

  /* Deterministic randomness, so the field is the same on every load. */
  function makeRandom(seed) {
    var state = seed >>> 0;
    return function () {
      state = (state + 0x6d2b79f5) >>> 0;
      var t = state;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function rgbOf(hex) {
    return { r: (hex >> 16) & 255, g: (hex >> 8) & 255, b: hex & 255 };
  }

  var BG_RGB = rgbOf(BG);

  /* Fade a colour toward the page background. Vertex colours carry no alpha,
     so a fade is expressed as a blend to the background instead. */
  function blend(hex, alpha) {
    var c = rgbOf(hex);
    var a = alpha < 0 ? 0 : alpha > 1 ? 1 : alpha;
    return [
      (BG_RGB.r + (c.r - BG_RGB.r) * a) / 255,
      (BG_RGB.g + (c.g - BG_RGB.g) * a) / 255,
      (BG_RGB.b + (c.b - BG_RGB.b) * a) / 255
    ];
  }

  function clamp01(value) {
    return value < 0 ? 0 : value > 1 ? 1 : value;
  }

  function buildBackground(THREE) {
    var material = new THREE.ShaderMaterial({
      uniforms: {
        uInner: { value: new THREE.Color(0x141414) },
        uOuter: { value: new THREE.Color(0x0a0a0a) },
        uAccent: { value: 0.035 }
      },
      vertexShader: [
        "varying vec2 vUv;",
        "void main() {",
        "  vUv = uv;",
        "  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);",
        "}"
      ].join("\n"),
      fragmentShader: [
        "varying vec2 vUv;",
        "uniform vec3 uInner;",
        "uniform vec3 uOuter;",
        "uniform float uAccent;",
        "void main() {",
        "  vec2 p = vUv - 0.5;",
        "  float d = length(p * vec2(1.0, 0.85));",
        "  float t = smoothstep(0.0, 0.72, d);",
        "  vec3 color = mix(uInner, uOuter, t);",
        "  color += vec3(0.208, 0.816, 0.729) * uAccent *",
        "    (1.0 - smoothstep(0.0, 0.34, d));",
        "  gl_FragColor = vec4(color, 1.0);",
        "}"
      ].join("\n"),
      depthWrite: false,
      depthTest: false
    });
    var mesh = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), material);
    mesh.position.z = -1;
    mesh.renderOrder = -1;
    mesh.frustumCulled = false;
    return mesh;
  }

  function mount(canvas, options) {
    var THREE = global.THREE;
    if (!THREE || !canvas) {
      return null;
    }

    var opts = options || {};
    var reduced = prefersReducedMotion();
    var settleAt = opts.settleAt || 7600;

    var renderer;
    try {
      renderer = new THREE.WebGLRenderer({
        canvas: canvas,
        antialias: true,
        alpha: false
      });
    } catch (error) {
      if (canvas.parentNode) {
        canvas.parentNode.classList.add("is-flat");
      }
      return null;
    }

    renderer.setClearColor(BG, 1);

    var scene = new THREE.Scene();
    var camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 10);
    camera.position.z = 5;
    scene.add(buildBackground(THREE));

    var viewW = 2;
    var viewH = 2;
    var aspect = 1;

    var nodes = [];
    var links = [];

    var nodeGeometry = new THREE.BufferGeometry();
    var nodePositions = new Float32Array(0);
    var nodeColors = new Float32Array(0);
    var nodeMaterial = new THREE.PointsMaterial({
      size: 2.4,
      sizeAttenuation: false,
      vertexColors: true,
      transparent: false
    });
    var nodePoints = new THREE.Points(nodeGeometry, nodeMaterial);
    nodePoints.frustumCulled = false;
    scene.add(nodePoints);

    var linkGeometry = new THREE.BufferGeometry();
    var linkPositions = new Float32Array(0);
    var linkColors = new Float32Array(0);
    var linkMaterial = new THREE.LineBasicMaterial({
      vertexColors: true,
      transparent: false
    });
    var linkLines = new THREE.LineSegments(linkGeometry, linkMaterial);
    linkLines.frustumCulled = false;
    scene.add(linkLines);

    function measure() {
      var w = canvas.clientWidth || (canvas.parentNode && canvas.parentNode.clientWidth) || global.innerWidth || 1;
      var h = canvas.clientHeight || (canvas.parentNode && canvas.parentNode.clientHeight) || global.innerHeight || 1;
      renderer.setPixelRatio(Math.min(global.devicePixelRatio || 1, 2));
      renderer.setSize(w, h, false);
      aspect = w / h;
      viewH = 2;
      viewW = viewH * aspect;
      camera.left = -viewW / 2;
      camera.right = viewW / 2;
      camera.top = viewH / 2;
      camera.bottom = -viewH / 2;
      camera.updateProjectionMatrix();
      var background = scene.children[0];
      background.scale.set(viewW, viewH, 1);
    }

    function buildField() {
      var rand = makeRandom(0x5c37a1);
      var count = Math.max(
        48,
        Math.min(100, Math.round(70 * Math.min(1.3, Math.max(0.75, aspect))))
      );

      nodes = [];
      var i;
      for (i = 0; i < count; i += 1) {
        nodes.push({
          bx: (rand() - 0.5) * viewW * 0.86,
          by: (rand() - 0.5) * viewH * 0.8,
          amp: 0.016 + rand() * 0.03,
          speed: 0.00028 + rand() * 0.00042,
          phase: rand() * Math.PI * 2,
          birth: 220 + (i / count) * 1500 + rand() * 240
        });
      }

      /* Nearest neighbours first, and no node carries more than three links,
         so the field reads as structure rather than a mesh. */
      var threshold = viewH * 0.155;
      var candidates = [];
      var a;
      var b;
      for (a = 0; a < count; a += 1) {
        for (b = a + 1; b < count; b += 1) {
          var dx = nodes[a].bx - nodes[b].bx;
          var dy = nodes[a].by - nodes[b].by;
          var distance = Math.sqrt(dx * dx + dy * dy);
          if (distance < threshold) {
            candidates.push([distance, a, b]);
          }
        }
      }
      candidates.sort(function (left, right) {
        return left[0] - right[0];
      });

      var degree = new Array(count);
      for (i = 0; i < count; i += 1) {
        degree[i] = 0;
      }

      var maxLinks = Math.round(count * 1.15);
      links = [];
      for (i = 0; i < candidates.length && links.length < maxLinks; i += 1) {
        var candidate = candidates[i];
        var leftIndex = candidate[1];
        var rightIndex = candidate[2];
        if (degree[leftIndex] >= 2 || degree[rightIndex] >= 2) {
          continue;
        }
        degree[leftIndex] += 1;
        degree[rightIndex] += 1;

        var roll = rand();
        var detachAt = null;
        if (roll < 0.2) {
          /* A duplicate: it appears, then collapses. */
          detachAt = 0;
        } else if (roll < 0.3) {
          /* A superseded value: it detaches when its replacement wins. */
          detachAt = 0;
        }

        var nodeLeft = nodes[leftIndex];
        var nodeRight = nodes[rightIndex];
        var born = Math.max(
          480 + (i / Math.max(1, candidates.length)) * 2400 + rand() * 220,
          nodeLeft.birth + 180,
          nodeRight.birth + 180
        );

        links.push({
          a: leftIndex,
          b: rightIndex,
          birth: born,
          accent: false,
          detachAt: detachAt === null ? null : born + 1500 + rand() * 1500
        });
      }

      /* The single accent, used sparingly: five links that survive. */
      var stride = Math.max(1, Math.floor(links.length / 5));
      var accented = 0;
      for (i = 2; i < links.length && accented < 5; i += stride) {
        if (links[i].detachAt === null) {
          links[i].accent = true;
          accented += 1;
        }
      }

      nodePositions = new Float32Array(count * 3);
      nodeColors = new Float32Array(count * 3);
      nodeGeometry.setAttribute(
        "position",
        new THREE.BufferAttribute(nodePositions, 3)
      );
      nodeGeometry.setAttribute("color", new THREE.BufferAttribute(nodeColors, 3));

      linkPositions = new Float32Array(links.length * 2 * 3);
      linkColors = new Float32Array(links.length * 2 * 3);
      linkGeometry.setAttribute(
        "position",
        new THREE.BufferAttribute(linkPositions, 3)
      );
      linkGeometry.setAttribute("color", new THREE.BufferAttribute(linkColors, 3));
    }

    var frameState = {
      pointer: null
    };

    function renderAt(elapsed) {
      var t = elapsed > settleAt ? settleAt : elapsed;
      var globalFade = clamp01(t / 700);
      var i;

      for (i = 0; i < nodes.length; i += 1) {
        var node = nodes[i];
        var born = clamp01((t - node.birth) / 600) * globalFade;
        var calm = 1 - clamp01((t - node.birth) / (settleAt - node.birth + 1)) * 0.92;
        var driftX = Math.sin(t * node.speed + node.phase) * node.amp * calm;
        var driftY =
          Math.cos(t * node.speed * 1.3 + node.phase) * node.amp * calm;
        var x = node.bx + driftX;
        var y = node.by + driftY;
        node.px = x;
        node.py = y;
        nodePositions[i * 3] = x;
        nodePositions[i * 3 + 1] = y;
        nodePositions[i * 3 + 2] = 0;
        var nodeColor = blend(NODE, 0.55 * born);
        nodeColors[i * 3] = nodeColor[0];
        nodeColors[i * 3 + 1] = nodeColor[1];
        nodeColors[i * 3 + 2] = nodeColor[2];
      }

      var vertex = 0;
      for (i = 0; i < links.length; i += 1) {
        var link = links[i];
        var linkBorn = clamp01((t - link.birth) / 700) * globalFade;
        var alpha = linkBorn;
        if (link.detachAt !== null) {
          alpha *= clamp01((link.detachAt - t) / 900);
        }
        if (alpha <= 0.003) {
          continue;
        }
        var linkColor = blend(link.accent ? ACCENT : LINE, alpha * (link.accent ? 0.8 : 0.9));
        var left = nodes[link.a];
        var right = nodes[link.b];
        var base = vertex * 6;
        linkPositions[base] = left.px;
        linkPositions[base + 1] = left.py;
        linkPositions[base + 2] = 0;
        linkPositions[base + 3] = right.px;
        linkPositions[base + 4] = right.py;
        linkPositions[base + 5] = 0;
        linkColors[base] = linkColor[0];
        linkColors[base + 1] = linkColor[1];
        linkColors[base + 2] = linkColor[2];
        linkColors[base + 3] = linkColor[0];
        linkColors[base + 4] = linkColor[1];
        linkColors[base + 5] = linkColor[2];
        vertex += 1;
      }

      nodeGeometry.attributes.position.needsUpdate = true;
      nodeGeometry.attributes.color.needsUpdate = true;
      linkGeometry.setDrawRange(0, vertex * 2);
      linkGeometry.attributes.position.needsUpdate = true;
      linkGeometry.attributes.color.needsUpdate = true;

      renderer.render(scene, camera);
    }

    var rafId = 0;
    var settleTimer = 0;
    var startedAt = 0;
    var running = false;
    var finished = false;

    /* One idempotent end state: the loop, the fallback timer and the resize
       path all land here. The fallback matters when rAF is throttled (a
       background tab) and would otherwise leave the scene unsettled. */
    function finish() {
      if (finished) {
        return;
      }
      finished = true;
      running = false;
      if (rafId) {
        global.cancelAnimationFrame(rafId);
        rafId = 0;
      }
      renderAt(settleAt);
      canvas.setAttribute("data-settled", "true");
    }

    function loop(now) {
      if (finished) {
        return;
      }
      if (!startedAt) {
        startedAt = now;
      }
      var elapsed = now - startedAt;
      frameState.pointer = elapsed;
      renderAt(elapsed);
      if (elapsed >= settleAt) {
        finish();
        return;
      }
      rafId = global.requestAnimationFrame(loop);
    }

    function start() {
      measure();
      buildField();
      if (reduced) {
        finish();
        return;
      }
      running = true;
      settleTimer = global.setTimeout(finish, settleAt + 250);
      rafId = global.requestAnimationFrame(loop);
    }

    var resizeTimer = 0;
    function onResize() {
      if (resizeTimer) {
        global.clearTimeout(resizeTimer);
      }
      resizeTimer = global.setTimeout(function () {
        resizeTimer = 0;
        measure();
        buildField();
        var elapsed = running ? frameState.pointer || 0 : settleAt;
        renderAt(elapsed);
      }, 180);
    }
    global.addEventListener("resize", onResize);

    start();

    return {
      settleAt: settleAt,
      stop: function () {
        running = false;
        finished = true;
        if (rafId) {
          global.cancelAnimationFrame(rafId);
        }
        if (settleTimer) {
          global.clearTimeout(settleTimer);
        }
        global.removeEventListener("resize", onResize);
      }
    };
  }

  global.ChetaHero = {
    mount: mount,
    prefersReducedMotion: prefersReducedMotion
  };

  /* The standalone intro page mounts itself. */
  function bootIntro() {
    var canvas = document.getElementById("intro-canvas");
    if (!canvas) {
      return;
    }
    mount(canvas, { settleAt: 9600 });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootIntro);
  } else {
    bootIntro();
  }
})(window);
