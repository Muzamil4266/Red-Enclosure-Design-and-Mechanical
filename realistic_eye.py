#!/usr/bin/env python3
"""
Realistic Eye (standalone & mixin) - Shoby Red Project
======================================================
A lifelike eye drawn live on the GPU: wet glossy cornea with a window reflection, iris with fibres and a dark
limbal ring, bloodshot-able sclera, eyelids with a crease and a wet rim, eyelashes, a pupil that breathes, and
a human blink (fast close, slower open, sometimes a double or half blink; the eyeball rolls up while it blinks).
The eye follows your mouse, or wanders by itself when you switch that off.

Only 7 controls (panel on the left, press H to hide it):
  eye colour (5 swatches) - pupil size - blink rate - redness - skin tone - light - follow mouse
Keys:  SPACE or click = blink     H = hide / show panel     R = reset everything     ESC = quit

Run:        python realistic_eye.py
Install:    pip install pygame moderngl numpy
"""
import argparse
import math
import os
import random
import subprocess
import sys
import time

import numpy as np
import pygame
import moderngl

# =============================================================================================
# shaders
# =============================================================================================
VERT = """
#version 330
in vec2 in_pos;
void main() { gl_Position = vec4(in_pos, 0.0, 1.0); }
"""

FRAG = r"""
#version 330
uniform vec2  u_res;
uniform float u_time;
uniform float u_open;     // 1 open ... 0 closed
uniform vec2  u_gaze;     // yaw, pitch (radians), +x right, +y up
uniform float u_pupil;    // 0..1
uniform vec3  u_irisA;    // outer iris colour
uniform vec3  u_irisB;    // inner (around the pupil) colour
uniform float u_red;      // sclera redness
uniform float u_skin;     // 0 light ... 1 dark
uniform float u_light;    // exposure
out vec4 fragColor;

const float W  = 0.74;    // half width of the eye opening
const float PI = 3.14159265;

// ---------------------------------------------------------------- noise
float hash13(vec3 p) {
    p = fract(p * 0.1031);
    p += dot(p, p.zyx + 31.32);
    return fract((p.x + p.y) * p.z);
}
float hash11(float n) { return fract(sin(n * 127.1) * 43758.5453); }
float vnoise(vec3 p) {
    vec3 i = floor(p), f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(mix(hash13(i), hash13(i + vec3(1,0,0)), f.x),
                   mix(hash13(i + vec3(0,1,0)), hash13(i + vec3(1,1,0)), f.x), f.y),
               mix(mix(hash13(i + vec3(0,0,1)), hash13(i + vec3(1,0,1)), f.x),
                   mix(hash13(i + vec3(0,1,1)), hash13(i + vec3(1,1,1)), f.x), f.y), f.z);
}
float fbm(vec3 p) {
    float a = 0.5, s = 0.0;
    for (int i = 0; i < 5; i++) { s += a * vnoise(p); p = p * 2.03 + vec3(7.1, 3.7, 1.3); a *= 0.5; }
    return s;
}

// ---------------------------------------------------------------- eyelid shapes
float archU(float u) { return pow(max(sin(PI * pow(clamp(u, 0.0, 1.0), 0.72)), 0.0), 0.85); }
float archL(float u) { return pow(max(sin(PI * pow(clamp(u, 0.0, 1.0), 1.45)), 0.0), 0.90); }
float tiltY(float u) { return 0.07 * (u - 0.5); }
float uOf(float x)   { return (x + W) / (2.0 * W); }

float upperOpenY(float x) { float u = uOf(x); return tiltY(u) + (0.245 + 0.12 * u_gaze.y) * archU(u); }
float lowerOpenY(float x) { float u = uOf(x); return tiltY(u) - (0.22 - 0.03 * u_gaze.y) * archL(u); }
float closedY(float x)    { float u = uOf(x); return tiltY(u) - 0.075 * sin(PI * clamp(u, 0.0, 1.0)); }
float upperY(float x) {
    float c = 1.0 - u_open;
    return mix(upperOpenY(x), closedY(x) - 0.040, pow(c, 0.9));
}
float lowerY(float x) {
    float c = 1.0 - u_open;
    return mix(lowerOpenY(x), closedY(x), 0.80 * c);
}

// ---------------------------------------------------------------- what the cornea reflects (a window)
vec3 envLight(vec3 r) {
    float rz = max(r.z, 0.15);
    vec2 w = r.xy / rz;
    vec2 c = vec2(-0.50, 0.52);
    vec2 hs = vec2(0.25, 0.19);
    vec2 d = abs(w - c) - hs + 0.06;
    float sd = length(max(d, 0.0)) + min(max(d.x, d.y), 0.0) - 0.06;     // rounded rectangle
    float rect = 1.0 - smoothstep(-0.012, 0.026, sd);
    float grad = 0.75 + 0.25 * clamp((w.y - c.y) / hs.y * 0.5 + 0.5, 0.0, 1.0);
    float halo = exp(-max(sd, 0.0) * 16.0) * 1.6;
    float win = rect * grad * 17.0 + halo;
    vec2 e2 = w - vec2(0.75, -0.55);
    float soft = exp(-dot(e2, e2) * 5.0) * 1.1;
    float sky = mix(0.05, 0.45, clamp(r.y * 0.5 + 0.5, 0.0, 1.0));
    return vec3(1.0, 0.98, 0.94) * win + vec3(0.90, 0.95, 1.0) * soft + vec3(0.30, 0.34, 0.40) * sky;
}

// ---------------------------------------------------------------- iris
vec3 irisColor(float t, float th, float pupilT) {
    vec2 cs = vec2(cos(th), sin(th));
    float f1 = fbm(vec3(cs * 6.0, t * 2.0));
    float f2 = vnoise(vec3(cs * 26.0, t * 4.0));
    float f3 = vnoise(vec3(cs * 60.0, t * 7.0));
    float fib = f1 * 0.55 + f2 * 0.30 + f3 * 0.15;
    float col = 0.50 + 0.09 * (fbm(vec3(cs * 4.0, 3.7)) - 0.5) * 2.0;     // jagged collarette ring
    float zone = smoothstep(col - 0.06, col + 0.06, t);
    vec3 inner = u_irisB * (0.80 + 0.75 * fib);
    vec3 outer = u_irisA * (0.50 + 1.30 * fib * fib + 0.45 * fib);
    vec3 c = mix(inner, outer, zone);
    float fur = smoothstep(0.58, 0.82, fbm(vec3(cs * 16.0, t * 1.4 + 5.0)));
    c *= 1.0 - 0.38 * fur * smoothstep(0.30, 0.65, t);
    float cr = smoothstep(0.66, 0.80, vnoise(vec3(cs * 8.0, t * 8.0 + 11.0)));
    c *= 1.0 - 0.50 * cr * smoothstep(col - 0.22, col - 0.02, t) * (1.0 - smoothstep(col, col + 0.20, t));
    float bright = smoothstep(0.78, 0.9, vnoise(vec3(cs * 34.0, t * 5.0 + 20.0)));
    c += 0.10 * bright * zone;
    float limb = smoothstep(0.62, 0.96, t);
    c = mix(c, c * vec3(0.10, 0.12, 0.16), limb * 0.95);
    c *= mix(0.50, 1.0, smoothstep(pupilT, pupilT + 0.12, t));
    return c;
}

vec3 scleraColor(vec2 p, vec3 n) {
    float m = fbm(vec3(p * 3.2, 2.0));
    vec3 c = mix(vec3(0.98, 0.965, 0.94), vec3(0.90, 0.84, 0.76), smoothstep(0.35, 0.80, m));
    vec2 q = p * vec2(1.0, 1.5);
    float warp = fbm(vec3(q * 4.0, 7.0));
    float ridge = abs(fbm(vec3(q * 9.0 + warp * 1.8, 4.0)) - 0.5);
    float vein = 1.0 - smoothstep(0.0, 0.028, ridge);
    float zc = smoothstep(0.30, 0.95, abs(p.x) / W);
    vein *= (0.20 + 0.80 * zc) * u_red;
    c = mix(c, vec3(0.74, 0.20, 0.18), vein * 0.70);
    c = mix(c, vec3(0.90, 0.60, 0.56), u_red * 0.30 * zc);
    return c;
}

// the visible eyeball; spec = glossy reflections (added in linear light)
vec3 eyeSurface(vec2 p, float yu, float yl, float aa, out vec3 spec) {
    vec2 q = vec2(p.x / 0.80, p.y / 0.58);
    float nz = sqrt(max(1.0 - min(dot(q, q), 0.985), 0.015));
    vec3 n = normalize(vec3(q, nz));
    float yaw = u_gaze.x, pit = u_gaze.y;
    vec3 gz = normalize(vec3(sin(yaw) * cos(pit), sin(pit), cos(yaw) * cos(pit)));
    vec3 gx = normalize(cross(vec3(0, 1, 0), gz));
    vec3 gy = cross(gz, gx);
    vec3 nl = vec3(dot(n, gx), dot(n, gy), dot(n, gz));
    float r = length(nl.xy);
    float th = atan(nl.y, nl.x);
    float ri = 0.43;
    float fw = max(fwidth(r), 0.0015);
    float irisM = (1.0 - smoothstep(ri - fw * 1.2, ri + fw * 1.2, r)) * step(0.0, nl.z);
    float pupilT = mix(0.26, 0.62, u_pupil) * (1.0 + 0.04 * (vnoise(vec3(cos(th) * 3.0, sin(th) * 3.0, u_time * 0.4)) - 0.5));

    vec3 L = normalize(vec3(-0.5, 0.6, 0.62));
    float diff = max(dot(n, L), 0.0);
    float edgeShade = mix(0.62, 1.0, smoothstep(0.05, 0.65, nz));

    float t = r / ri * 0.95;
    vec3 iris = irisColor(t, th, pupilT);
    float pupilM = 1.0 - smoothstep(pupilT - 0.012, pupilT + 0.012, t);
    iris = mix(iris, vec3(0.006, 0.006, 0.008), pupilM);
    // light focused through the cornea shows up on the far side of the iris
    vec2 cdir = vec2(0.62, -0.78);
    float caustic = smoothstep(0.55, 0.98, dot(normalize(nl.xy + 1e-4), cdir)) * smoothstep(0.55, 0.90, t) * (1.0 - smoothstep(0.90, 1.0, t));
    iris += u_irisA * 0.45 * caustic * (1.0 - pupilM);

    vec3 scl = scleraColor(p, n);
    scl *= (0.66 + 0.40 * diff) * edgeShade;
    iris *= (0.80 + 0.30 * diff);

    vec3 col = mix(scl, iris, irisM);

    // lid shadow (ambient occlusion)
    float sU = smoothstep(0.0, 0.13, yu - p.y);
    float sL = smoothstep(0.0, 0.06, p.y - yl);
    col *= mix(0.42, 1.0, sU) * mix(0.80, 1.0, sL);

    // caruncle (pink bump in the inner corner)
    vec2 cc = (p - vec2(-0.60, -0.025)) / vec2(0.085, 0.075);
    float car = exp(-dot(cc, cc) * 1.3);
    col = mix(col, vec3(0.80, 0.38, 0.36) * (0.65 + 0.35 * vnoise(vec3(p * 60.0, 1.0))), car * 0.85);

    // glossy reflections
    vec3 v = vec3(0.0, 0.0, -1.0);
    float rcM = (1.0 - smoothstep(0.50 - fw, 0.50 + fw, r)) * step(0.0, nl.z);
    vec2 tl = nl.xy * 1.54;
    vec3 ncl = vec3(tl, sqrt(max(1.0 - min(dot(tl, tl), 0.95), 0.05)));
    vec3 nc = normalize(gx * ncl.x + gy * ncl.y + gz * ncl.z);
    vec3 rf = reflect(v, nc);
    float F = 0.025 + 0.975 * pow(clamp(1.0 - nc.z, 0.0, 1.0), 5.0);
    spec = envLight(rf) * F * rcM;
    vec3 ns = normalize(n + 0.05 * (vec3(vnoise(vec3(p * 30.0, 2.0)), vnoise(vec3(p * 30.0, 5.0)), 0.0) - 0.5));
    float Fs = 0.02 + 0.98 * pow(clamp(1.0 - ns.z, 0.0, 1.0), 5.0);
    spec += envLight(reflect(v, ns)) * Fs * (1.0 - rcM) * 0.10 * mix(0.2, 1.0, sU);
    // tear meniscus along the lower lid
    float tear = smoothstep(yl + 0.016, yl, p.y) * (0.55 + 0.45 * vnoise(vec3(p.x * 50.0, 0.0, 3.0)));
    spec += vec3(0.30, 0.30, 0.32) * tear;
    spec *= mix(0.15, 1.0, sU);
    return col;
}

// ---------------------------------------------------------------- skin around the eye
vec3 skinColor(vec2 p, float yu, float yl) {
    vec3 lightS = vec3(0.89, 0.73, 0.65), darkS = vec3(0.42, 0.27, 0.21);
    vec3 base = mix(lightS, darkS, u_skin);
    base *= 0.93 + 0.14 * fbm(vec3(p * 1.6, 9.0));
    base *= 0.96 + 0.08 * vnoise(vec3(p * 210.0, 3.0));
    base *= 0.90 + 0.16 * (0.5 + (-0.25 * p.x + 0.45 * p.y));
    float u = uOf(p.x);
    float lidMask = smoothstep(0.03, 0.12, u) * (1.0 - smoothstep(0.90, 0.99, u));
    float o = u_open;
    float du = p.y - yu;
    float dl = yl - p.y;
    float creaseY = yu + 0.105 * (0.55 + 0.45 * o);
    float creaseAmt = smoothstep(-0.05, 0.25, u) * (1.0 - smoothstep(0.78, 1.02, u));
    float crease = exp(-pow((p.y - creaseY) / 0.024, 2.0));
    base *= 1.0 - 0.46 * crease * creaseAmt;
    base *= 1.0 - 0.13 * exp(-pow((p.y - (yu + 0.20)) / 0.075, 2.0)) * creaseAmt;
    base *= 1.0 + 0.07 * exp(-pow((p.y - (yu + 0.045)) / 0.03, 2.0)) * creaseAmt;
    base *= 1.0 + 0.07 * smoothstep(creaseY, creaseY + 0.14, p.y) * (1.0 - smoothstep(creaseY + 0.14, creaseY + 0.5, p.y));
    base *= 1.0 - 0.10 * smoothstep(0.0, 0.02, du) * (1.0 - smoothstep(0.02, 0.10, du)) * lidMask;
    float redNear = (exp(-max(du, 0.0) / 0.05) * step(0.0, du) + exp(-max(dl, 0.0) / 0.03) * step(0.0, dl)) * lidMask;
    base = mix(base, base * vec3(1.05, 0.80, 0.78) + vec3(0.03, 0.0, 0.0), clamp(redNear, 0.0, 1.0) * 0.45);
    base *= 1.0 - 0.15 * exp(-pow((dl - 0.075) / 0.05, 2.0)) * step(0.0, dl);
    base *= 1.0 + 0.04 * exp(-pow((dl - 0.15) / 0.05, 2.0)) * step(0.0, dl);
    float socket = smoothstep(-0.95, -0.55, p.x) * 0.0 + exp(-pow((p.x + 0.82) / 0.20, 2.0)) * 0.18;
    base *= 1.0 - socket;
    return base;
}

// ---------------------------------------------------------------- eyelashes
float lashes(vec2 p, float aa, bool upper) {
    int N = upper ? 56 : 34;
    float c = 1.0 - u_open;
    float cov = 0.0;
    float ic0 = floor(uOf(p.x) * float(N));
    for (int k = -7; k <= 7; k++) {
        float fi = ic0 + float(k);
        if (fi < 0.0 || fi >= float(N)) continue;
        float h1 = hash11(fi + (upper ? 1.0 : 77.0));
        float h2 = hash11(fi * 1.7 + (upper ? 5.0 : 91.0));
        if (!upper && h2 < 0.30) continue;
        float ui = (fi + 0.5 + (h1 - 0.5) * 0.6) / float(N);
        float xb = -W + ui * 2.0 * W;
        float yb = upper ? upperY(xb) : lowerY(xb);
        float e = 0.01;
        float ya = upper ? upperY(xb + e) : lowerY(xb + e);
        float yc = upper ? upperY(xb - e) : lowerY(xb - e);
        vec2 t = normalize(vec2(1.0, (ya - yc) / (2.0 * e)));
        vec2 nrm = upper ? vec2(-t.y, t.x) : vec2(t.y, -t.x);
        float s = (ui - 0.30) * (upper ? 1.05 : 0.50);
        if (!upper) s = -s;
        vec2 d = vec2(nrm.x * cos(s) + nrm.y * sin(s), -nrm.x * sin(s) + nrm.y * cos(s));
        if (upper) d = normalize(mix(d, normalize(vec2(d.x * 0.5, -0.85)), c * 0.85));
        vec2 pn = vec2(-d.y, d.x);
        float L = (upper ? 0.150 : 0.060) * (0.50 + 0.50 * sin(PI * pow(clamp(ui, 0.0, 1.0), 0.75))) * (0.65 + 0.55 * h2);
        float curl = (upper ? 0.85 : 0.25) * (0.4 + h1) * (upper ? -1.0 : 1.0);
        vec2 w = p - vec2(xb, yb);
        float a = dot(w, d);
        float b = dot(w, pn);
        if (a > -0.004 && a < L) {
            float sp = clamp(a / L, 0.0, 1.0);
            float dist = abs(b - curl * a * a / L);
            float width = mix(0.0080, 0.0010, pow(sp, 0.7)) * (upper ? 1.0 : 0.6) * (h1 > 0.78 ? 1.6 : 1.0);
            float cv = (1.0 - smoothstep(width * 0.5, width * 0.5 + aa * 0.9, dist)) * smoothstep(-0.004, 0.0, a);
            cv *= 1.0 - 0.45 * smoothstep(0.85, 1.0, sp);
            cov = max(cov, cv);
        }
    }
    return cov;
}

void main() {
    vec2 frag = gl_FragCoord.xy;
    vec2 p = (frag - 0.5 * u_res) / u_res.y * 1.22;
    float aa = 1.5 / u_res.y * 1.22;

    float yu = upperY(p.x);
    float yl = lowerY(p.x);
    float inside = smoothstep(yl - aa, yl + aa, p.y) * (1.0 - smoothstep(yu - aa, yu + aa, p.y));
    inside *= smoothstep(0.0, 0.004, yu - yl);          // no hairline where the lids meet at the corners

    vec3 skin = skinColor(p, yu, yl);
    vec3 spec = vec3(0.0);
    vec3 eye = skin;
    if (inside > 0.0) {
        vec3 sp;
        eye = eyeSurface(p, yu, yl, aa, sp);
        spec = sp * inside;
    }
    vec3 col = mix(skin, eye, inside);

    // lid margins (the thin wet edge of each lid), only between the two corners
    float gap = yu - yl;
    float uu = uOf(p.x);
    float inU = smoothstep(0.015, 0.06, uu) * (1.0 - smoothstep(0.94, 0.985, uu));
    float rimVis = smoothstep(0.0, 0.03, gap) * inU;
    float rw = 0.026;
    float mU = smoothstep(yu - aa, yu + aa, p.y) * (1.0 - smoothstep(yu + rw - aa, yu + rw + aa, p.y));
    vec3 rimU = mix(vec3(0.20, 0.10, 0.09), skin * 0.92, smoothstep(yu, yu + rw, p.y));
    col = mix(col, rimU, mU * 0.95 * rimVis);
    float lw = 0.032;
    float mL = smoothstep(yl - lw - aa, yl - lw + aa, p.y) * (1.0 - smoothstep(yl - aa, yl + aa, p.y));
    vec3 rimL = mix(skin * vec3(1.02, 0.86, 0.84), vec3(0.93, 0.66, 0.62), smoothstep(yl - lw, yl - 0.008, p.y));
    col = mix(col, rimL, mL * 0.95 * rimVis);
    spec += vec3(0.42) * smoothstep(yl - 0.012, yl - 0.002, p.y) * (1.0 - smoothstep(yl - 0.002, yl + 0.004, p.y))
            * rimVis * (0.45 + 0.55 * vnoise(vec3(p.x * 40.0, 0.0, 8.0)));

    // closed-lid seam
    float cF = smoothstep(0.55, 1.0, 1.0 - u_open);
    float ys = 0.5 * (yu + yl);
    float seamAmt = smoothstep(0.02, 0.10, uOf(p.x)) * (1.0 - smoothstep(0.90, 0.98, uOf(p.x)));
    col *= 1.0 - 0.55 * exp(-pow((p.y - ys) / 0.006, 2.0)) * cF * seamAmt;

    // eyelashes
    float cov = 0.0;
    if (abs(p.y - yu) < 0.34 && abs(p.x) < W + 0.35) cov = max(cov, lashes(p, aa, true));
    if (abs(p.y - yl) < 0.16 && abs(p.x) < W + 0.15) cov = max(cov, lashes(p, aa, false));
    col = mix(col, vec3(0.030, 0.021, 0.018), cov * 0.96);
    spec *= 1.0 - cov;

    // to linear light, add glossy reflections, tone map, back to display
    col = pow(col, vec3(2.2)) * u_light * 1.12 + spec;
    vec3 hi = vec3(0.70) + 0.30 * (1.0 - exp(-(col - 0.70) / 0.30));
    col = mix(col, hi, step(vec3(0.70), col));
    col = pow(col, vec3(1.0 / 2.2));
    float vig = smoothstep(1.35, 0.30, length(p));
    col *= mix(0.70, 1.0, vig);
    col += (hash13(vec3(frag, u_time * 60.0)) - 0.5) * 0.012;
    fragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
}
"""

UI_VERT = """
#version 330
in vec2 in_pos;
in vec2 in_uv;
out vec2 v_uv;
void main() { v_uv = in_uv; gl_Position = vec4(in_pos, 0.0, 1.0); }
"""
UI_FRAG = """
#version 330
uniform sampler2D tex;
in vec2 v_uv;
out vec4 fragColor;
void main() { fragColor = texture(tex, v_uv); }
"""

# =============================================================================================
# settings
# =============================================================================================
EYE_COLORS = {
    "Blue":  ((0.40, 0.55, 0.70), (0.66, 0.54, 0.36)),
    "Green": ((0.28, 0.50, 0.30), (0.62, 0.52, 0.22)),
    "Hazel": ((0.45, 0.42, 0.22), (0.65, 0.42, 0.18)),
    "Brown": ((0.30, 0.17, 0.08), (0.46, 0.27, 0.10)),
    "Grey":  ((0.45, 0.52, 0.58), (0.62, 0.56, 0.46)),
}
DEFAULTS = {"color": "Blue", "pupil": 0.38, "blink_rate": 16.0, "red": 0.30, "skin": 0.18, "light": 1.0, "follow": True}
SLIDERS = [  # key, label, min, max, number format
    ("pupil", "Pupil size", 0.0, 1.0, "{:.2f}"),
    ("blink_rate", "Blinks per minute", 4.0, 40.0, "{:.0f}"),
    ("red", "Redness", 0.0, 1.0, "{:.2f}"),
    ("skin", "Skin tone", 0.0, 1.0, "{:.2f}"),
    ("light", "Light", 0.5, 1.8, "{:.2f}"),
]


# =============================================================================================
# GPU renderer
# =============================================================================================
class EyeRenderer:
    def __init__(self, ctx):
        self.ctx = ctx
        self.prog = ctx.program(vertex_shader=VERT, fragment_shader=FRAG)
        quad = np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4")
        self.vao = ctx.simple_vertex_array(self.prog, ctx.buffer(quad.tobytes()), "in_pos")

    def draw(self, size, t, open_, gaze, pupil, color_name, red, skin, light):
        a, b = EYE_COLORS[color_name]
        p = self.prog
        p["u_res"].value = tuple(float(s) for s in size)
        p["u_time"].value = float(t)
        p["u_open"].value = float(open_)
        p["u_gaze"].value = (float(gaze[0]), float(gaze[1]))
        p["u_pupil"].value = float(pupil)
        p["u_irisA"].value = a
        p["u_irisB"].value = b
        p["u_red"].value = float(red)
        p["u_skin"].value = float(skin)
        p["u_light"].value = float(light)
        self.ctx.viewport = (0, 0, int(size[0]), int(size[1]))
        self.vao.render(moderngl.TRIANGLE_STRIP)


# =============================================================================================
# behaviour: blinking and eye movement
# =============================================================================================
def smooth(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3.0 - 2.0 * x)


class Blinker:
    """Human-like blinking: close ~90 ms, hold ~30 ms, open ~200 ms (slower), 15 % double blinks, 10 % half blinks."""

    def __init__(self):
        self.active = False
        self.t = 0.0
        self.depth = 1.0
        self.queue = 0
        self.wait = random.uniform(1.0, 3.0)
        self.speed = 1.0

    def trigger(self, depth=1.0, double=False):
        if not self.active:
            self.active, self.t, self.depth = True, 0.0, depth
            self.queue = 1 if double else 0

    def closure(self):
        if not self.active:
            return 0.0
        c, h, o = 0.09 / self.speed, 0.03 / self.speed, 0.20 / self.speed
        if self.t < c:
            k = smooth(self.t / c)
        elif self.t < c + h:
            k = 1.0
        else:
            q = min(1.0, (self.t - c - h) / o)
            k = (1.0 - q) ** 1.6                    # opens fast at first, settles slowly
        return self.depth * k

    def update(self, dt, rate_per_min):
        if self.active:
            self.t += dt
            if self.t > (0.09 + 0.03 + 0.20) / self.speed:
                self.active = False
                if self.queue:
                    self.queue -= 1
                    self.wait = 0.10
                else:
                    self.wait = (60.0 / max(rate_per_min, 1.0)) * random.uniform(0.45, 1.55)
        else:
            self.wait -= dt
            if self.wait <= 0:
                depth = 0.55 if random.random() < 0.10 else 1.0
                self.trigger(depth, double=random.random() < 0.15)
        return self.closure()


class Gaze:
    """Eyeball direction: follows the mouse (fast, saccade-like) or wanders by itself, with tiny drifts."""

    def __init__(self):
        self.pos = [0.0, 0.0]
        self.target = [0.0, 0.0]
        self.timer = 0.0
        self.t = 0.0

    def update(self, dt, mouse_target):
        self.t += dt
        if mouse_target is not None:
            tgt = mouse_target
        else:
            self.timer -= dt
            if self.timer <= 0:
                self.timer = random.uniform(0.7, 2.6)
                if random.random() < 0.35:
                    self.target = [random.uniform(-0.06, 0.06), random.uniform(-0.04, 0.04)]
                else:
                    self.target = [random.uniform(-0.32, 0.32), random.uniform(-0.18, 0.20)]
            tgt = self.target
        k = 1.0 - math.exp(-dt * 16.0)
        self.pos[0] += (tgt[0] - self.pos[0]) * k
        self.pos[1] += (tgt[1] - self.pos[1]) * k
        dx = 0.008 * math.sin(self.t * 1.3) + 0.004 * math.sin(self.t * 2.9 + 1.0)
        dy = 0.006 * math.sin(self.t * 1.1 + 2.0) + 0.003 * math.sin(self.t * 3.3)
        return self.pos[0] + dx, self.pos[1] + dy


# =============================================================================================
# small control panel (drawn with pygame, shown as a texture)
# =============================================================================================
class Panel:
    W = 292

    def __init__(self, settings):
        self.s = settings
        self.visible = True
        self.drag = None
        self.dirty = True
        pygame.font.init()
        self.f_title = self._font(21, True)
        self.f = self._font(15, False)
        self.f_small = self._font(13, False)
        self.rects = {}
        self.layout()

    @staticmethod
    def _font(size, bold):
        try:
            return pygame.font.SysFont("segoeui,arial,dejavusans,liberationsans", size, bold=bold)
        except Exception:
            return pygame.font.Font(None, size + 4)

    def layout(self):
        y = 14
        self.rects["title"] = (16, y)
        y += 36
        self.rects["color_label"] = (16, y)
        y += 24
        self.swatches = {}
        x = 16
        for name in EYE_COLORS:
            self.swatches[name] = pygame.Rect(x, y, 46, 30)
            x += 52
        y += 46
        self.sliders = {}
        for key, label, lo, hi, fmt in SLIDERS:
            self.rects[key + "_label"] = (16, y)
            self.sliders[key] = pygame.Rect(16, y + 26, self.W - 32, 10)
            y += 54
        self.follow_rect = pygame.Rect(16, y + 2, 22, 22)
        y += 38
        self.btn_blink = pygame.Rect(16, y, 124, 34)
        self.btn_reset = pygame.Rect(152, y, 124, 34)
        y += 48
        self.H = y

    def contains(self, pos):
        return self.visible and 0 <= pos[0] <= self.W and 0 <= pos[1] <= self.H

    def _set_slider(self, key, x):
        rect = self.sliders[key]
        lo, hi = next((l, h) for k, _, l, h, _ in SLIDERS if k == key)
        v = min(1.0, max(0.0, (x - rect.x) / rect.w))
        self.s[key] = lo + v * (hi - lo)
        self.dirty = True

    def mouse_down(self, pos, on_blink, on_reset):
        if not self.contains(pos):
            return False
        for name, r in self.swatches.items():
            if r.collidepoint(pos):
                self.s["color"] = name
                self.dirty = True
        for key, r in self.sliders.items():
            if r.inflate(0, 22).collidepoint(pos):
                self.drag = key
                self._set_slider(key, pos[0])
        if self.follow_rect.inflate(110, 8).collidepoint(pos):
            self.s["follow"] = not self.s["follow"]
            self.dirty = True
        if self.btn_blink.collidepoint(pos):
            on_blink()
        if self.btn_reset.collidepoint(pos):
            on_reset()
            self.dirty = True
        return True

    def mouse_move(self, pos):
        if self.drag:
            self._set_slider(self.drag, pos[0])

    def mouse_up(self):
        self.drag = None

    def render(self):
        surf = pygame.Surface((self.W, self.H), pygame.SRCALPHA)
        pygame.draw.rect(surf, (12, 12, 15, 215), surf.get_rect(), border_radius=14)
        pygame.draw.rect(surf, (139, 69, 19, 255), surf.get_rect(), width=2, border_radius=14)
        surf.blit(self.f_title.render("Realistic Eye", True, (224, 160, 112)), self.rects["title"])
        surf.blit(self.f.render("Eye colour", True, (205, 205, 210)), self.rects["color_label"])
        for name, r in self.swatches.items():
            a, b = EYE_COLORS[name]
            pygame.draw.rect(surf, tuple(int(c * 255) for c in a), r, border_radius=8)
            pygame.draw.circle(surf, tuple(int(c * 255) for c in b), r.center, 6)
            pygame.draw.circle(surf, (8, 8, 8), r.center, 3)
            if name == self.s["color"]:
                pygame.draw.rect(surf, (255, 255, 255), r.inflate(4, 4), width=2, border_radius=9)
        for key, label, lo, hi, fmt in SLIDERS:
            x, y = self.rects[key + "_label"]
            surf.blit(self.f.render(label, True, (205, 205, 210)), (x, y))
            val = fmt.format(self.s[key])
            txt = self.f.render(val, True, (241, 196, 15))
            surf.blit(txt, (self.W - 16 - txt.get_width(), y))
            r = self.sliders[key]
            pygame.draw.rect(surf, (60, 60, 66), r, border_radius=5)
            frac = (self.s[key] - lo) / (hi - lo)
            pygame.draw.rect(surf, (139, 69, 19), (r.x, r.y, max(4, int(r.w * frac)), r.h), border_radius=5)
            pygame.draw.circle(surf, (255, 255, 255), (r.x + int(r.w * frac), r.centery), 9)
        pygame.draw.rect(surf, (60, 60, 66), self.follow_rect, border_radius=5)
        if self.s["follow"]:
            pygame.draw.rect(surf, (139, 69, 19), self.follow_rect.inflate(-6, -6), border_radius=3)
        surf.blit(self.f.render("Eye follows mouse", True, (205, 205, 210)), (self.follow_rect.right + 10, self.follow_rect.y + 1))
        for rect, text, color in ((self.btn_blink, "Blink (Space)", (31, 106, 165)), (self.btn_reset, "Reset all", (139, 69, 19))):
            pygame.draw.rect(surf, color, rect, border_radius=8)
            t = self.f_small.render(text, True, (255, 255, 255))
            surf.blit(t, (rect.centerx - t.get_width() // 2, rect.centery - t.get_height() // 2))
        self.dirty = False
        return surf


class PanelDrawer:
    """Draws the panel surface on top of the eye with alpha blending."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.prog = ctx.program(vertex_shader=UI_VERT, fragment_shader=UI_FRAG)
        self.buf = ctx.buffer(reserve=4 * 4 * 4)
        self.vao = ctx.simple_vertex_array(self.prog, self.buf, "in_pos", "in_uv")
        self.tex = None

    def upload(self, surf):
        data = pygame.image.tostring(surf, "RGBA", True)
        if self.tex is None or self.tex.size != surf.get_size():
            if self.tex:
                self.tex.release()
            self.tex = self.ctx.texture(surf.get_size(), 4)
            self.tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.tex.write(data)

    def draw(self, win_size, panel_size, margin=14):
        if self.tex is None:
            return
        ww, wh = win_size
        pw, ph = panel_size
        x0, y0 = margin, margin
        def ndc(px, py):
            return (px / ww * 2.0 - 1.0, 1.0 - py / wh * 2.0)
        (l, t), (r, b) = ndc(x0, y0), ndc(x0 + pw, y0 + ph)
        verts = np.array([l, b, 0, 0, r, b, 1, 0, l, t, 0, 1, r, t, 1, 1], dtype="f4")
        self.buf.write(verts.tobytes())
        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
        self.tex.use(0)
        self.vao.render(moderngl.TRIANGLE_STRIP)
        self.ctx.disable(moderngl.BLEND)


# =============================================================================================
# window / main loop
# =============================================================================================
def open_window(size):
    pygame.init()
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_CORE)
    pygame.display.set_mode(size, pygame.OPENGL | pygame.DOUBLEBUF | pygame.RESIZABLE)
    pygame.display.set_caption("Realistic Eye")
    return moderngl.create_context()


def grab_frame(ctx, size):
    raw = ctx.screen.read(viewport=(0, 0, size[0], size[1]), components=3, alignment=1)
    return pygame.image.fromstring(raw, size, "RGB", True)


def run_shot(args):
    size = tuple(int(v) for v in args.size.lower().split("x"))
    ctx = open_window(size)
    r = EyeRenderer(ctx)
    for _ in range(2):
        r.draw(size, 1.0, args.open, (args.yaw, args.pitch), args.pupil, args.color, args.red, args.skin, args.light)
    pygame.image.save(grab_frame(ctx, size), args.shot)
    print("saved", args.shot)


def main():
    ap = argparse.ArgumentParser(description="Realistic Eye")
    ap.add_argument("--shot", help="save one image to this file and exit")
    ap.add_argument("--size", default="1100x700")
    ap.add_argument("--open", type=float, default=1.0)
    ap.add_argument("--yaw", type=float, default=0.0)
    ap.add_argument("--pitch", type=float, default=0.0)
    ap.add_argument("--pupil", type=float, default=DEFAULTS["pupil"])
    ap.add_argument("--color", default=DEFAULTS["color"], choices=list(EYE_COLORS))
    ap.add_argument("--red", type=float, default=DEFAULTS["red"])
    ap.add_argument("--skin", type=float, default=DEFAULTS["skin"])
    ap.add_argument("--light", type=float, default=DEFAULTS["light"])
    args = ap.parse_args()
    if args.shot:
        return run_shot(args)

    size = (1100, 700)
    ctx = open_window(size)
    renderer = EyeRenderer(ctx)
    drawer = PanelDrawer(ctx)
    settings = dict(DEFAULTS)
    panel = Panel(settings)
    blinker, gaze = Blinker(), Gaze()
    clock = pygame.time.Clock()
    t0 = time.time()

    def reset():
        settings.update(DEFAULTS)

    running = True
    while running:
        dt = min(clock.tick(60) / 1000.0, 0.1)
        win = pygame.display.get_window_size()
        mouse = pygame.mouse.get_pos()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    running = False
                elif ev.key == pygame.K_SPACE:
                    blinker.trigger()
                elif ev.key == pygame.K_h:
                    panel.visible = not panel.visible
                elif ev.key == pygame.K_r:
                    reset()
                    panel.dirty = True
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                if not panel.mouse_down(ev.pos, blinker.trigger, reset):
                    blinker.trigger()
            elif ev.type == pygame.MOUSEMOTION:
                panel.mouse_move(ev.pos)
            elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
                panel.mouse_up()

        target = None
        if settings["follow"] and pygame.mouse.get_focused() and not panel.contains(mouse):
            nx = (mouse[0] - win[0] / 2) / (win[0] / 2)
            ny = (mouse[1] - win[1] / 2) / (win[1] / 2)
            target = [max(-1.0, min(1.0, nx)) * 0.42, -max(-1.0, min(1.0, ny)) * 0.30]
        closure = blinker.update(dt, settings["blink_rate"])
        gx, gy = gaze.update(dt, target)
        gy += 0.22 * closure                      # the eyeball rolls up while the lids close
        t = time.time() - t0
        pupil = settings["pupil"] * (1.0 - 0.12 * (settings["light"] - 1.0)) + 0.025 * math.sin(t * 1.7) + 0.015 * math.sin(t * 4.3)
        pupil = min(1.0, max(0.0, pupil))

        ctx.clear(0.0, 0.0, 0.0)
        renderer.draw(win, t, 1.0 - closure, (gx, gy), pupil, settings["color"], settings["red"], settings["skin"], settings["light"])
        if panel.visible:
            if panel.dirty or drawer.tex is None:
                drawer.upload(panel.render())
            drawer.draw(win, (panel.W, panel.H))
        pygame.display.flip()
    pygame.quit()


class RealisticEyeMixin:
    """Mixin class for launching Realistic Eye inside Red Enclosure."""

    def init_realistic_eye(self):
        self._real_eye_proc = None

    def open_real_eye(self):
        """Launches Realistic Eye in a dedicated GPU process."""
        script_path = os.path.join(os.path.dirname(__file__), "realistic_eye.py")
        if not os.path.exists(script_path):
            script_path = "realistic_eye.py"
        try:
            subprocess.Popen([sys.executable, script_path])
        except Exception as e:
            if hasattr(self, "toast"):
                self.toast(f"Error launching Real Eye: {e}")


if __name__ == "__main__":
    main()
