"""
On-screen HUD animation — a small always-on-top window styled like a dense
sci-fi heads-up display: layered dashed rings, major/minor tick readouts,
a faint circuit/constellation node field, corner light beams, HUD text
labels, and a glowing pulsing core. Speeds up / brightens with state.

Runs a pygame window loop. Must be driven from the main thread (pygame's
event pump is not reliably thread-safe on Windows), so callers run the audio
and gesture loops on background threads and call `run()` here last, on the
main thread — it blocks until the window is closed.

States: "idle" (waiting for wake word/gesture), "listening" (greeting/
recording), "active" (thinking/speaking). Purely cosmetic — never affects
the assistant's behavior, so a windowing failure here should never take down
voice.py itself; callers wrap construction/run in try/except.
"""

from __future__ import annotations

import math
import random
import threading

_BG = (2, 7, 10)
_CYAN = (70, 235, 240)
_CYAN_DIM = (25, 95, 100)
_CYAN_FAINT = (14, 55, 60)
_WHITE = (215, 255, 255)

# speed = rotation/pulse rate multiplier, glow = core brightness/size, per state
_STATE_PARAMS = {
    "idle": {"speed": 0.6, "glow": 0.55},
    "listening": {"speed": 1.7, "glow": 0.8},
    "active": {"speed": 2.8, "glow": 1.0},
}

_HEX_CODES = "0123456789ABCDEF"


def _hex_points(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    return [
        (cx + r * math.cos(math.pi / 3 * i), cy + r * math.sin(math.pi / 3 * i))
        for i in range(6)
    ]


def _rand_hex(n: int) -> str:
    return "".join(random.choice(_HEX_CODES) for _ in range(n))


class OrbAnimation:
    def __init__(self, size: tuple[int, int] = (480, 480), title: str = "Jarvis"):
        self.size = size
        self.title = title
        self._state = "idle"
        self._lock = threading.Lock()
        self._running = False

        # fixed per-instance layout for the circuit/star field, so it doesn't
        # reshuffle every frame
        rng = random.Random(0)
        self._stars = [
            (rng.uniform(0.15, 0.98), rng.uniform(0, 2 * math.pi), rng.uniform(0.5, 1.6))
            for _ in range(50)
        ]
        self._nodes = [
            (rng.uniform(0.3, 0.95), rng.uniform(0, 2 * math.pi))
            for _ in range(9)
        ]

    def set_state(self, state: str) -> None:
        if state not in _STATE_PARAMS:
            return
        with self._lock:
            self._state = state

    def stop(self) -> None:
        self._running = False

    # ---- decorative layers -------------------------------------------

    def _draw_starfield(self, screen, cx: int, cy: int, max_r: float, t: float) -> None:
        import pygame

        for frac, angle, tw_speed in self._stars:
            r = frac * max_r
            x = cx + math.cos(angle) * r
            y = cy + math.sin(angle) * r
            twinkle = 0.5 + 0.5 * math.sin(t * tw_speed * 2 + angle * 5)
            shade = int(40 + 60 * twinkle)
            pygame.draw.circle(screen, (shade // 2, shade, shade), (int(x), int(y)), 1)

    def _draw_circuit_nodes(self, screen, cx: int, cy: int, max_r: float, t: float) -> None:
        """Faint constellation of nodes connected to their nearest neighbor —
        a tech/circuit-board texture behind the main rings."""
        import pygame

        pts = []
        for frac, angle in self._nodes:
            r = frac * max_r
            x = cx + math.cos(angle) * r
            y = cy + math.sin(angle) * r
            pts.append((x, y))

        for i, (x, y) in enumerate(pts):
            nx, ny = pts[(i + 1) % len(pts)]
            pygame.draw.line(screen, _CYAN_FAINT, (x, y), (nx, ny), 1)
            pulse = 0.5 + 0.5 * math.sin(t * 1.5 + i)
            r = 2 + pulse * 1.5
            pygame.draw.circle(screen, _CYAN_DIM, (int(x), int(y)), int(r), width=1)

    def _draw_corner_beams(self, screen, glow_surface, cx: int, cy: int, t: float) -> None:
        """Faint diagonal light beams from the four corners, pulsing slowly."""
        import pygame

        w, h = self.size
        corners = [(0, 0), (w, 0), (0, h), (w, h)]
        pulse = 0.5 + 0.5 * math.sin(t * 0.4)
        for corner_x, corner_y in corners:
            alpha = int(16 + 10 * pulse)
            dx, dy = cx - corner_x, cy - corner_y
            length = math.hypot(dx, dy)
            ux, uy = dx / length, dy / length
            end_x = corner_x + ux * length * 0.55
            end_y = corner_y + uy * length * 0.55
            pygame.draw.line(glow_surface, (*_CYAN, alpha), (corner_x, corner_y),
                              (end_x, end_y), 3)

    def _draw_hex_accents(self, screen, cx: int, cy: int, max_r: float) -> None:
        """Static hexagon accents around the bezel, like a HUD corner readout."""
        import pygame

        for i in range(6):
            angle = math.pi / 3 * i + math.pi / 6
            hx = cx + math.cos(angle) * (max_r * 0.98)
            hy = cy + math.sin(angle) * (max_r * 0.98)
            pygame.draw.polygon(screen, _CYAN_DIM, _hex_points(hx, hy, 9), width=1)

    def _draw_dashed_ring(self, screen, cx: int, cy: int, radius: float,
                           color, num_dashes: int, dash_frac: float,
                           rotation: float, width: int = 1) -> None:
        import pygame

        rect = pygame.Rect(cx - radius, cy - radius, radius * 2, radius * 2)
        step = (2 * math.pi) / num_dashes
        for i in range(num_dashes):
            start = rotation + i * step
            end = start + step * dash_frac
            try:
                pygame.draw.arc(screen, color, rect, start, end, width)
            except ValueError:
                pass  # degenerate rect at tiny radii; skip that frame

    def _draw_ticks(self, screen, cx: int, cy: int, radius: float,
                     rotation: float) -> None:
        """Compass-style ticks: a long tick every 30°, short ticks every 10°."""
        import pygame

        for deg in range(0, 360, 10):
            angle = rotation + math.radians(deg)
            major = deg % 30 == 0
            length = 14 if major else 6
            color = _CYAN if major else _CYAN_DIM
            x1 = cx + math.cos(angle) * radius
            y1 = cy + math.sin(angle) * radius
            x2 = cx + math.cos(angle) * (radius + length)
            y2 = cy + math.sin(angle) * (radius + length)
            pygame.draw.line(screen, color, (x1, y1), (x2, y2), 2 if major else 1)

    def _draw_readout(self, font, screen, cx: int, cy: int, max_r: float, t: float) -> None:
        """Small monospace HUD readouts — status text and a rolling hex code."""
        labels = [
            ("SYS//ONLINE", (cx - max_r * 0.95, cy - max_r * 1.02)),
            (f"0x{_rand_hex(4)}", (cx + max_r * 0.55, cy - max_r * 1.02)),
            (f"FREQ {abs(math.sin(t * 0.7)) * 42 + 10:5.1f}", (cx - max_r * 0.95, cy + max_r * 0.97)),
            ("J.A.R.V.I.S", (cx + max_r * 0.45, cy + max_r * 0.97)),
        ]
        for text, (x, y) in labels:
            surf = font.render(text, True, _CYAN_DIM)
            screen.blit(surf, (x, y))

    def _draw_core(self, screen, glow_surface, cx: int, cy: int,
                    max_r: float, t: float, speed: float, glow: float) -> None:
        import pygame

        pulse = math.sin(t * speed * 2) * 0.06 + 1.0
        core_r = max_r * 0.16 * pulse * (0.85 + 0.15 * glow)

        # layered additive glow, biggest/faintest first
        for i in range(8, 0, -1):
            r = core_r * (1 + i * 0.4)
            alpha = int(16 * glow * (9 - i) / 8)
            pygame.draw.circle(glow_surface, (*_CYAN, alpha), (cx, cy), int(r))

        # crosshair flare through the core
        flare_len = core_r * 2.4
        for ang in (0, math.pi / 2):
            pygame.draw.line(
                glow_surface, (*_WHITE, int(30 * glow)),
                (cx - math.cos(ang) * flare_len, cy - math.sin(ang) * flare_len),
                (cx + math.cos(ang) * flare_len, cy + math.sin(ang) * flare_len), 1,
            )

        pygame.draw.circle(screen, _WHITE, (cx, cy), int(core_r * 0.5))
        pygame.draw.circle(screen, _CYAN, (cx, cy), int(core_r), width=2)
        pygame.draw.circle(screen, _CYAN_DIM, (cx, cy), int(core_r * 1.5), width=1)

    def _draw_sweep(self, screen, glow_surface, cx: int, cy: int,
                     max_r: float, t: float, speed: float) -> None:
        """Radar-style rotating wedge with a fading trail."""
        import pygame

        angle = t * speed * 1.3
        trail = 0.55  # radians of fading trail behind the leading edge
        steps = 22
        for i in range(steps):
            frac = i / steps
            a0 = angle - trail * frac
            a1 = angle - trail * (i + 1) / steps
            alpha = int(70 * (1 - frac))
            points = [
                (cx, cy),
                (cx + math.cos(a0) * max_r, cy + math.sin(a0) * max_r),
                (cx + math.cos(a1) * max_r, cy + math.sin(a1) * max_r),
            ]
            pygame.draw.polygon(glow_surface, (*_CYAN, alpha), points)

    def _draw_orbiters(self, screen, cx: int, cy: int, max_r: float,
                        t: float, speed: float, count: int = 6) -> None:
        import pygame

        radius = max_r * 0.58
        for k in range(count):
            angle = t * speed * 0.5 + k * (2 * math.pi / count)
            px = cx + math.cos(angle) * radius
            py = cy + math.sin(angle) * radius
            if k % 2 == 0:
                pygame.draw.circle(screen, _WHITE, (int(px), int(py)), 3)
                pygame.draw.circle(screen, _CYAN, (int(px), int(py)), 6, width=1)
            else:
                size = 5
                rect = pygame.Rect(px - size, py - size, size * 2, size * 2)
                pygame.draw.rect(screen, _CYAN, rect, width=1)

    # ---- main loop ------------------------------------------------------

    def run(self) -> None:
        """Blocking window loop. Call on the main thread."""
        import pygame

        pygame.init()
        pygame.display.set_caption(self.title)
        screen = pygame.display.set_mode(self.size)
        glow_surface = pygame.Surface(self.size, pygame.SRCALPHA)
        clock = pygame.time.Clock()
        try:
            font = pygame.font.SysFont("consolas", 12)
        except Exception:
            font = pygame.font.Font(None, 14)

        cx, cy = self.size[0] // 2, self.size[1] // 2
        max_r = min(self.size) / 2 - 34

        t = 0.0
        self._running = True

        while self._running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self._running = False

            with self._lock:
                state = self._state
            params = _STATE_PARAMS[state]
            speed, glow = params["speed"], params["glow"]

            screen.fill(_BG)
            glow_surface.fill((0, 0, 0, 0))

            self._draw_corner_beams(screen, glow_surface, cx, cy, t)
            self._draw_starfield(screen, cx, cy, max_r * 1.15, t)
            self._draw_circuit_nodes(screen, cx, cy, max_r * 1.05, t)
            self._draw_hex_accents(screen, cx, cy, max_r)

            # outer static bezel ring with compass ticks
            pygame.draw.circle(screen, _CYAN_DIM, (cx, cy), int(max_r), width=1)
            self._draw_ticks(screen, cx, cy, max_r, t * speed * 0.05)

            # concentric dashed rings, alternating spin direction/density
            self._draw_dashed_ring(screen, cx, cy, max_r * 0.90, _CYAN, 32, 0.5,
                                    t * speed * 0.35, width=2)
            self._draw_dashed_ring(screen, cx, cy, max_r * 0.78, _CYAN_DIM, 24, 0.6,
                                    -t * speed * 0.55)
            self._draw_dashed_ring(screen, cx, cy, max_r * 0.64, _CYAN, 18, 0.45,
                                    t * speed * 0.8)
            self._draw_dashed_ring(screen, cx, cy, max_r * 0.50, _CYAN_DIM, 10, 0.65,
                                    -t * speed * 1.1)

            self._draw_sweep(screen, glow_surface, cx, cy, max_r * 0.90, t, speed)
            self._draw_orbiters(screen, cx, cy, max_r, t, speed)
            self._draw_core(screen, glow_surface, cx, cy, max_r, t, speed, glow)

            screen.blit(glow_surface, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)
            self._draw_readout(font, screen, cx, cy, max_r, t)

            pygame.display.flip()
            t += clock.tick(60) / 1000.0

        pygame.quit()
