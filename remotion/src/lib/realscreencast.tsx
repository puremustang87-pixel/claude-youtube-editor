// RealScreencast — wrap a REAL screen capture (.mp4) in the same branded chrome
// the fake screencast uses, so genuine proof (live results, real latency, output
// you can't credibly fake) flows through the identical word-synced system. This
// is the counterpart to lib/screencast.tsx: that one simulates a recording from
// static screenshots; this one plays an actual OffthreadVideo capture and layers
// the kit's zoom / highlight / click-pulse passes on top.
//
// Render-safe by construction: OffthreadVideo (not <Video>) so frames extract
// deterministically under the bulk renderer; muted defaults TRUE so the master
// cut's narration is never fought by the capture's own audio.
//
// Coordinate convention (matches lib/screencast.tsx so the two are interchangeable
// in an author's head): every position/size is a FRACTION (0..1), so it survives
// any resize of the video box.
//   zoom cx/cy        -> fraction of the VIDEO (transform-origin + recenter of the push)
//   highlight x/y/w/h -> fraction of the VIDEO (a rect over the footage)
//   clickPulse x/y    -> fraction of the VIDEO (where the ring blooms)
// Frame-based only; monotonic interpolate + clamp (see vidtsx-2d-generator).
import React from 'react';
import {
  AbsoluteFill, OffthreadVideo, interpolate, staticFile, useCurrentFrame, useVideoConfig,
} from 'remotion';
import { COLORS, EASINGS, RADIUS, SHADOW } from '../brand';
import { FONT_BODY } from '../fonts';
import { WebBrowserFrame, chromeH } from './browser';
import { BrandBg, CLAMP } from './kit';

// ---- keyframe/rect types (fraction-based; see header) -----------------------
// A zoom keyframe: at absolute `frame`, push to `scale` centered on (cx, cy) —
// both fractions of the video. scale 1 = fit (no push). Keys are sorted by
// construction (see RealScreencast) so interpolate inputs stay monotonic.
export type ZoomKey = { frame: number; cx: number; cy: number; scale: number };

// A highlight rect (fractions of the video), shown for [fromFrame, toFrame].
export type HighlightRect = {
  fromFrame: number; toFrame: number;
  x: number; y: number; w: number; h: number;
};

// A click emphasis: an expanding ring blooms at (x, y) fraction, at `frame`.
export type ClickPulse = { frame: number; x: number; y: number };

const PULSE_DUR = 18; // frames a click ring takes to bloom out (matches screencast.tsx ripple)

// Sample a piecewise zoom path at `frame`. Eased per segment (easeInOut, brand
// motion). Returns { cx, cy, scale } — all in video fractions. Because we sort +
// dedupe the keys before sampling, the per-segment interpolate inputs [a,b] are
// always strictly increasing (crash rule) and the search is well-defined.
const sampleZoom = (frame: number, keys: ZoomKey[]) => {
  if (!keys.length) return { cx: 0.5, cy: 0.5, scale: 1 };
  if (frame <= keys[0].frame) return { cx: keys[0].cx, cy: keys[0].cy, scale: keys[0].scale };
  const last = keys[keys.length - 1];
  if (frame >= last.frame) return { cx: last.cx, cy: last.cy, scale: last.scale };
  let i = 0;
  while (i < keys.length - 1 && keys[i + 1].frame <= frame) i++;
  const a = keys[i], b = keys[i + 1];
  const t = interpolate(frame, [a.frame, b.frame], [0, 1], { ...CLAMP, easing: EASINGS.easeInOut });
  return {
    cx: a.cx + (b.cx - a.cx) * t,
    cy: a.cy + (b.cy - a.cy) * t,
    scale: a.scale + (b.scale - a.scale) * t,
  };
};

// The zoomed video layer. Fills its parent (region for "browser", the rounded
// card for "none"); scales about the focal fraction AND recenters so the focal
// point stays put as we push in — the natural "lean into this result" move.
const ZoomedVideo: React.FC<{
  src: string;         // already-resolved URL (output of staticFile)
  trimBefore: number;  // frames (Remotion 4.0.319+ name; was `startFrom`)
  trimAfter?: number;  // frames (was `endAt`)
  playbackRate: number;
  muted: boolean;
  zoom: ZoomKey[];
}> = ({ src, trimBefore, trimAfter, playbackRate, muted, zoom }) => {
  const frame = useCurrentFrame();
  const z = sampleZoom(frame, zoom);
  // recenter: as scale grows past 1, translate so the focal fraction holds its
  // on-screen position. At scale 1 the offset is 0 (pure fit).
  const tx = (0.5 - z.cx) * (z.scale - 1) * 100;
  const ty = (0.5 - z.cy) * (z.scale - 1) * 100;
  return (
    <div style={{
      position: 'absolute', inset: 0, overflow: 'hidden',
      transform: `translate(${tx}%, ${ty}%) scale(${z.scale})`,
      transformOrigin: `${z.cx * 100}% ${z.cy * 100}%`,
    }}>
      <OffthreadVideo
        src={src}
        trimBefore={trimBefore}
        trimAfter={trimAfter}
        playbackRate={playbackRate}
        muted={muted}
        style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
      />
    </div>
  );
};

// Brand-colored rounded outline over a fraction rect, with a subtle backdrop dim
// on the rest of the frame so the eye goes to the box. Coordinates are fractions
// of the layer (region or card). Fades in/out on its [fromFrame, toFrame] window.
const Highlight: React.FC<{ rect: HighlightRect; color: string }> = ({ rect, color }) => {
  const frame = useCurrentFrame();
  if (frame < rect.fromFrame - 12 || frame > rect.toFrame + 12) return null;
  const op = Math.min(
    interpolate(frame, [rect.fromFrame - 10, rect.fromFrame], [0, 1], { ...CLAMP, easing: EASINGS.easeOut }),
    interpolate(frame, [rect.toFrame, rect.toFrame + 10], [1, 0], { ...CLAMP, easing: EASINGS.easeIn }),
  );
  const sc = interpolate(frame, [rect.fromFrame - 10, rect.fromFrame + 4], [1.05, 1], { ...CLAMP, easing: EASINGS.easeOut });
  const left = `${rect.x * 100}%`, top = `${rect.y * 100}%`;
  const width = `${rect.w * 100}%`, height = `${rect.h * 100}%`;
  return (
    <div style={{ position: 'absolute', inset: 0, opacity: op, pointerEvents: 'none' }}>
      {/* backdrop dim with a hole punched over the rect (box-shadow spread trick) */}
      <div style={{
        position: 'absolute', left, top, width, height, borderRadius: 12,
        boxShadow: '0 0 0 100vmax rgba(10,10,20,0.34)',
      }} />
      {/* the brand outline */}
      <div style={{
        position: 'absolute', left, top, width, height, borderRadius: 12,
        border: `3px solid ${color}`, transform: `scale(${sc})`, transformOrigin: 'center',
        boxShadow: `0 0 0 6px ${color}22`,
      }} />
    </div>
  );
};

// Expanding ring pulse at a fraction point — click emphasis on real footage,
// mirroring the screencast.tsx click ripple (brand accent, ~18f bloom).
const ClickRing: React.FC<{ pulse: ClickPulse; color: string }> = ({ pulse, color }) => {
  const frame = useCurrentFrame();
  if (frame < pulse.frame || frame > pulse.frame + PULSE_DUR) return null;
  const sc = interpolate(frame, [pulse.frame, pulse.frame + PULSE_DUR], [0, 2.4], { ...CLAMP, easing: EASINGS.easeOut });
  const op = interpolate(frame, [pulse.frame, pulse.frame + PULSE_DUR], [0.5, 0], { ...CLAMP });
  return (
    <div style={{
      position: 'absolute', left: `${pulse.x * 100}%`, top: `${pulse.y * 100}%`,
      width: 34, height: 34, marginLeft: -17, marginTop: -17, borderRadius: '50%',
      border: `2px solid ${color}`, opacity: op, transform: `scale(${sc})`, pointerEvents: 'none',
    }} />
  );
};

export const RealScreencast: React.FC<{
  // required: staticFile-relative path to the capture,
  // e.g. 'projects/video-1/captures/latency.mp4'
  src: string;
  // trim the capture (seconds of source footage); converted to frames at comp fps.
  startFromSec?: number;
  endAtSec?: number;
  playbackRate?: number;
  // default TRUE — the master cut's voice must never fight the capture's audio.
  muted?: boolean;
  // "browser" wraps the capture in the kit's WebBrowserFrame; "none" is a bare
  // rounded-corner + shadow card (the kit's standard media treatment).
  chrome?: 'browser' | 'none';
  // passed through to WebBrowserFrame when chrome === 'browser'
  url?: React.ReactNode;
  tabTitle?: string;
  favicon?: React.ReactNode;
  // fraction-based passes (see header). zoom is re-sorted by frame internally so
  // the interpolate inputs are guaranteed monotonic no matter how they're listed.
  zoom?: ZoomKey[];
  highlights?: HighlightRect[];
  clickPulses?: ClickPulse[];
  // layout + look
  box?: { x: number; y: number; w: number; h: number };
  glow?: string;
  accent?: string; // brand color for highlights + click rings (default accent)
  appearAt?: number;
  uiScale?: number; // forwarded to WebBrowserFrame chrome scaling
}> = ({
  src,
  startFromSec = 0,
  endAtSec,
  playbackRate = 1,
  muted = true,
  chrome = 'browser',
  url = '',
  tabTitle = '',
  favicon,
  zoom = [],
  highlights = [],
  clickPulses = [],
  box = { x: 60, y: 63, w: 1800, h: 954 },
  glow = COLORS.signal,
  accent = COLORS.accent,
  appearAt = 0,
  uiScale = 1,
}) => {
  const { fps } = useVideoConfig();
  const frame = useCurrentFrame();

  // `src` is a staticFile-RELATIVE path (same contract as lib/screencast.tsx's
  // page.img); resolve it to a real URL for OffthreadVideo here.
  const resolvedSrc = staticFile(src);

  // seconds -> frames for OffthreadVideo trim (trimBefore/trimAfter are in
  // FRAMES). Frame inputs must be integers.
  const trimBefore = Math.max(0, Math.round(startFromSec * fps));
  const trimAfter = endAtSec !== undefined ? Math.round(endAtSec * fps) : undefined;

  // enforce monotonic zoom frames BY CONSTRUCTION: sort a copy by frame and drop
  // any duplicate-frame keys (a duplicated input would crash interpolate).
  const zoomKeys = React.useMemo(() => {
    const sorted = [...zoom].sort((a, b) => a.frame - b.frame);
    const out: ZoomKey[] = [];
    for (const k of sorted) if (!out.length || k.frame > out[out.length - 1].frame) out.push(k);
    return out;
  }, [zoom]);

  // the video + overlays, filling whatever layer wraps them (region or card)
  const inner = (
    <>
      <ZoomedVideo
        src={resolvedSrc}
        trimBefore={trimBefore}
        trimAfter={trimAfter}
        playbackRate={playbackRate}
        muted={muted}
        zoom={zoomKeys}
      />
      {highlights.map((h, i) => <Highlight key={`h${i}`} rect={h} color={accent} />)}
      {clickPulses.map((p, i) => <ClickRing key={`p${i}`} pulse={p} color={accent} />)}
    </>
  );

  if (chrome === 'browser') {
    // page region under the chrome, in the WebBrowserFrame's local box space. The
    // frame wraps children in a transformed (zero-height) div, so the layer needs
    // EXPLICIT region dims, not inset:0 (the same gotcha lib/screencast.tsx hits).
    const ch = chromeH(uiScale);
    const region = { w: box.w, h: box.h - ch };
    return (
      <AbsoluteFill style={{ fontFamily: FONT_BODY }}>
        <BrandBg glow={glow} />
        <WebBrowserFrame
          url={url}
          tabTitle={tabTitle}
          favicon={favicon}
          box={box}
          appearAt={appearAt}
          uiScale={uiScale}
        >
          <div style={{ position: 'absolute', top: 0, left: 0, width: region.w, height: region.h, overflow: 'hidden', background: '#000' }}>
            {inner}
          </div>
        </WebBrowserFrame>
      </AbsoluteFill>
    );
  }

  // chrome === 'none' — bare capture in the kit's standard rounded card + shadow.
  const op = interpolate(frame, [appearAt, appearAt + 14], [0, 1], { ...CLAMP, easing: EASINGS.easeOut });
  const y = interpolate(frame, [appearAt, appearAt + 16], [28, 0], { ...CLAMP, easing: EASINGS.easeOut });
  return (
    <AbsoluteFill style={{ fontFamily: FONT_BODY }}>
      <BrandBg glow={glow} />
      <div style={{
        position: 'absolute', left: box.x, top: box.y, width: box.w, height: box.h,
        borderRadius: RADIUS.card, overflow: 'hidden', border: `1px solid ${COLORS.line}`,
        boxShadow: SHADOW.card, background: '#000', opacity: op, transform: `translateY(${y}px)`,
      }}>
        {inner}
      </div>
    </AbsoluteFill>
  );
};
