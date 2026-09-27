/*
 * Sentiment word cloud: packs words inside a cloud-shaped bubble.
 *
 * Words are placed largest first on an elliptical spiral from the middle of
 * the bubble outward. A word lands at the first spot where its box stays
 * inside the silhouette and clear of every word already placed; a few
 * smaller words run vertically, like a classic word cloud.
 *
 * Usage:
 *   SentimentWordCloud.render(stageElement, words, {
 *     weightOf: w => number,          // size driver (required)
 *     toneOf:   w => 'pos'|'neu'|'neg',
 *     titleOf:  w => 'tooltip text',
 *     onSelect: w => {},              // optional: makes words clickable
 *     selected: 'word',               // optional: highlight one word
 *     animate:  true,
 *   });
 */
(function (global) {
  'use strict';

  var SVG_NS = 'http://www.w3.org/2000/svg';

  // Cloud silhouette as overlapping lobes, in fractions of the stage box:
  // [centerX, centerY, radiusX, radiusY].
  var LOBES = [
    [0.50, 0.66, 0.44, 0.25],
    [0.25, 0.56, 0.19, 0.27],
    [0.48, 0.41, 0.25, 0.36],
    [0.72, 0.48, 0.20, 0.30],
    [0.10, 0.70, 0.09, 0.15],
    [0.89, 0.68, 0.10, 0.17],
  ];

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function hash(str) {
    var h = 0;
    for (var i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) | 0;
    return Math.abs(h);
  }

  function layout(words, weightOf, W, H) {
    var lobes = LOBES.map(function (l) { return [l[0] * W, l[1] * H, l[2] * W, l[3] * H]; });
    function inside(x, y) {
      for (var i = 0; i < lobes.length; i++) {
        var dx = (x - lobes[i][0]) / lobes[i][2];
        var dy = (y - lobes[i][1]) / lobes[i][3];
        if (dx * dx + dy * dy <= 1) return true;
      }
      return false;
    }
    function boxInside(b) {
      var xs = [b.x0, (b.x0 + b.x1) / 2, b.x1];
      var ys = [b.y0, (b.y0 + b.y1) / 2, b.y1];
      for (var i = 0; i < 3; i++) for (var j = 0; j < 3; j++) if (!inside(xs[i], ys[j])) return false;
      return true;
    }
    function hits(b, placed) {
      for (var i = 0; i < placed.length; i++) {
        var p = placed[i];
        if (b.x0 < p.x1 && b.x1 > p.x0 && b.y0 < p.y1 && b.y1 > p.y0) return true;
      }
      return false;
    }

    var ctx = document.createElement('canvas').getContext('2d');
    var compact = W < 520;
    var minFont = compact ? 11 : 13;
    var maxFont = Math.max(26, Math.min(W * 0.075, 64));
    var max = weightOf(words[0]);
    var min = weightOf(words[words.length - 1]);
    function scale(w) { return max === min ? 0.55 : Math.sqrt((weightOf(w) - min) / (max - min)); }
    var originX = W * 0.49, originY = H * 0.57;
    var pad = compact ? 1.5 : 2.5;
    var placed = [];
    var out = [];

    words.forEach(function (w, rankIdx) {
      var s = scale(w);
      var weight = s > 0.6 ? 700 : s > 0.25 ? 600 : 500;
      var vertical = rankIdx > 3 && s < 0.5 && hash(w.t) % 4 === 0;
      var size = minFont + s * (maxFont - minFont);

      for (var attempt = 0; attempt < 3; attempt++) {
        ctx.font = weight + ' ' + size + 'px "IBM Plex Sans", sans-serif';
        var m = ctx.measureText(w.t);
        var ascent = m.actualBoundingBoxAscent || size * 0.72;
        var descent = m.actualBoundingBoxDescent || size * 0.2;
        var boxW = (vertical ? ascent + descent : m.width) + pad * 2;
        var boxH = (vertical ? m.width : ascent + descent) + pad * 2;

        // Step along the spiral by roughly constant arc length for even coverage.
        for (var t = 0, steps = 0; steps < 4000; steps++) {
          var r = t * 1.6;
          var x = originX + r * Math.cos(t) * 1.7;
          var y = originY + r * Math.sin(t);
          t += Math.min(0.4, 5 / Math.max(r, 1));
          if (r > W) break;
          var b = { x0: x - boxW / 2, x1: x + boxW / 2, y0: y - boxH / 2, y1: y + boxH / 2 };
          if (b.x0 < 0 || b.x1 > W || b.y0 < 0 || b.y1 > H) continue;
          if (!boxInside(b) || hits(b, placed)) continue;
          placed.push(b);
          out.push({ w: w, x: x, y: y, size: size, weight: weight, vertical: vertical, rankIdx: rankIdx, shift: (ascent - descent) / 2 });
          return;
        }
        size *= 0.82;
        if (size < minFont) break;
      }
    });
    return { lobes: lobes, words: out };
  }

  function render(stage, words, opts) {
    opts = opts || {};
    var weightOf = opts.weightOf;
    var toneOf = opts.toneOf || function () { return 'neu'; };
    var titleOf = opts.titleOf || function (w) { return w.t; };
    if (!stage) return;
    if (!words.length) { stage.innerHTML = ''; return; }

    var W = Math.max(280, Math.round(stage.clientWidth));
    var H = Math.round(Math.min(Math.max(W * 0.56, 220), 440));
    var result = layout(words, weightOf, W, H);
    var clickable = typeof opts.onSelect === 'function';

    var lobeEls = result.lobes.map(function (l) {
      return '<ellipse cx="' + l[0].toFixed(1) + '" cy="' + l[1].toFixed(1) + '" rx="' + l[2].toFixed(1) + '" ry="' + l[3].toFixed(1) + '"/>';
    }).join('');

    var textEls = result.words.map(function (p) {
      var rotate = p.vertical ? ' transform="rotate(-90 ' + p.x.toFixed(1) + ' ' + p.y.toFixed(1) + ')"' : '';
      var tracking = p.weight === 700 ? ' letter-spacing="-0.02em"' : '';
      var cls = 't-' + toneOf(p.w) + (opts.selected === p.w.t ? ' is-selected' : '');
      return '<text class="' + cls + '" data-word="' + esc(p.w.t) + '" x="' + p.x.toFixed(1) + '" y="' + (p.y + p.shift).toFixed(1) + '"'
        + ' text-anchor="middle" font-size="' + p.size.toFixed(1) + '" font-weight="' + p.weight + '"' + tracking + rotate
        + ' style="--i:' + p.rankIdx + '"><title>' + esc(titleOf(p.w)) + '</title>' + esc(p.w.t) + '</text>';
    }).join('');

    var cls = 'wc-svg' + (opts.animate === false ? ' no-anim' : '') + (clickable ? ' is-clickable' : '') + (opts.selected ? ' has-selection' : '');
    stage.innerHTML = '<svg class="' + cls + '" xmlns="' + SVG_NS + '" viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '">'
      + '<g class="wc-bubble"><g class="wc-bubble-edge">' + lobeEls + '</g><g class="wc-bubble-fill">' + lobeEls + '</g></g>'
      + '<g class="wc-words">' + textEls + '</g></svg>';

    if (clickable) {
      var byWord = {};
      words.forEach(function (w) { byWord[w.t] = w; });
      stage.querySelector('svg').addEventListener('click', function (e) {
        var el = e.target.closest('text[data-word]');
        if (el && byWord[el.getAttribute('data-word')]) opts.onSelect(byWord[el.getAttribute('data-word')]);
      });
    }
    return { placed: result.words.length, width: stage.clientWidth };
  }

  global.SentimentWordCloud = { render: render };
})(window);
