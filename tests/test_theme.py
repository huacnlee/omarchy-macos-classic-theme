import colorsys
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import tomllib
import unittest
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# `omarchy theme install` clones a repo straight into ~/.config/omarchy/themes/<name>
# and reads the theme from the files at the top level, so a repository can only
# carry one installable theme. The dark variant is that theme and lives at the
# root; the light variant stays in a subdirectory for install.sh to pick up.
DIRECTORIES = {
    "macos-classic-dark": ROOT,
    "macos-classic-light": ROOT / "macos-classic-light",
}

# omarchy derives the installed name from the repo name: omarchy-macos-classic-theme
# loses the omarchy- prefix and the -theme suffix.
INSTALLED_NAMES = {
    "macos-classic-dark": "macos-classic",
    "macos-classic-light": "macos-classic-light",
}

# Files that make up a theme. The repository root holds these plus the README,
# installer, tests, and the light variant.
THEME_FILES = {
    "backgrounds",
    "btop.theme",
    "chromium.theme",
    "colors.toml",
    "hyprland.lua",
    "icons.theme",
    "preview.png",
    "preview-unlock.png",
    "shell.hyprland.toml",
    "slack.theme",
    "unlock.png",
    "vscode.json",
    "zed.json",
}

VARIANTS = {
    "macos-classic-light": {
        "mode": "light",
        "background": "#F9F9F9",
        "foreground": "#000000",
        "accent": "#0060de",
        "chromium": "249,249,249",
        "vscode": "macOS Classic",
        "zed": "macOS Classic Light",
    },
    "macos-classic-dark": {
        "mode": "dark",
        "background": "#131313",
        "foreground": "#DEDEDE",
        "accent": "#077CFD",
        "chromium": "19,19,19",
        "vscode": "macOS Classic Dark v2",
        "zed": "macOS Classic Dark",
    },
}

# Longbridge keys its theme registry by theme name and reads user themes from
# ~/.longbridge/themes, so the repository carries one file, holding the dark
# variant only, and the installer lands it on omarchy.json.
LONGBRIDGE_THEME = "Omarchy System"
LONGBRIDGE_INSTALLED_NAME = "omarchy.json"

COLOR_KEYS = {
    "mode",
    "accent",
    "selection",
    "muted",
    "background",
    "dark_background",
    "darker_background",
    "lighter_background",
    "foreground",
    "dark_foreground",
    "light_foreground",
    "bright_foreground",
    "red",
    "yellow",
    "orange",
    "green",
    "cyan",
    "blue",
    "magenta",
    "brown",
    "bright_red",
    "bright_yellow",
    "bright_green",
    "bright_cyan",
    "bright_blue",
    "bright_magenta",
}


def theme_dir(name):
    return DIRECTORIES[name]


def load_palette(name):
    with (theme_dir(name) / "colors.toml").open("rb") as handle:
        return tomllib.load(handle)


def relative_luminance(hex_color):
    channels = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def is_neutral(hex_color, tolerance=8):
    channels = [int(hex_color[index : index + 2], 16) for index in (1, 3, 5)]
    return max(channels) - min(channels) <= tolerance


def contrast_ratio(first, second):
    high, low = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class PaletteTests(unittest.TestCase):
    def test_palettes_have_all_current_omarchy_keys(self):
        for name in VARIANTS:
            with self.subTest(name=name):
                self.assertEqual(COLOR_KEYS, set(load_palette(name)))

    def test_palettes_preserve_source_identity_colors(self):
        for name, expected in VARIANTS.items():
            with self.subTest(name=name):
                palette = load_palette(name)
                self.assertEqual(expected["mode"], palette["mode"])
                self.assertEqual(expected["background"].lower(), palette["background"].lower())
                self.assertEqual(expected["foreground"].lower(), palette["foreground"].lower())
                self.assertEqual(expected["accent"].lower(), palette["accent"].lower())

    def test_primary_text_contrast_meets_wcag_aa(self):
        for name in VARIANTS:
            with self.subTest(name=name):
                palette = load_palette(name)
                self.assertGreaterEqual(contrast_ratio(palette["foreground"], palette["background"]), 4.5)

    def test_muted_text_remains_readable(self):
        for name in VARIANTS:
            with self.subTest(name=name):
                palette = load_palette(name)
                self.assertGreaterEqual(contrast_ratio(palette["muted"], palette["background"]), 4.5)

    def test_light_palette_uses_macos_classic_neutral_surface_hierarchy(self):
        palette = load_palette("macos-classic-light")
        self.assertEqual("#F9F9F9", palette["background"])
        self.assertEqual("#F5F5F5", palette["lighter_background"])
        self.assertEqual("#E9E9E9", palette["dark_background"])
        self.assertEqual("#E0E0E0", palette["darker_background"])
        self.assertEqual("#0060de", palette["accent"])

    def test_dark_palette_is_anchored_to_the_source_editor_background(self):
        # #131313 is editor.background in the upstream macOS Classic theme; every
        # other dark surface sits below it so the desktop reads as one deep field.
        palette = load_palette("macos-classic-dark")
        self.assertEqual("#131313", palette["background"])
        self.assertEqual("#1B1B1B", palette["lighter_background"])
        self.assertEqual("#0D0D0D", palette["dark_background"])
        self.assertEqual("#080808", palette["darker_background"])
        for key in ("dark_background", "darker_background"):
            self.assertLess(
                relative_luminance(palette[key]), relative_luminance(palette["background"])
            )

    def test_light_muted_text_has_strong_readability(self):
        palette = load_palette("macos-classic-light")
        self.assertEqual("#555555", palette["muted"])
        self.assertEqual("#555555", palette["dark_foreground"])
        self.assertGreaterEqual(
            contrast_ratio(palette["muted"], palette["background"]), 7.0
        )


class TerminalPaletteTests(unittest.TestCase):
    ANSI_KEYS = (
        "red", "yellow", "orange", "green", "cyan", "blue", "magenta", "brown",
        "bright_red", "bright_yellow", "bright_green", "bright_cyan",
        "bright_blue", "bright_magenta",
    )

    def test_no_ansi_color_outshines_the_terminal_foreground(self):
        # A palette entry brighter than the text it sits beside glares; the
        # upstream neon cyan and pure yellow both broke this in dark mode.
        for name in VARIANTS:
            with self.subTest(name=name):
                palette = load_palette(name)
                ceiling = relative_luminance(palette["foreground"])
                for key in self.ANSI_KEYS:
                    if palette["mode"] == "dark":
                        self.assertLessEqual(
                            relative_luminance(palette[key]), ceiling,
                            f"{name} {key} ({palette[key]}) is brighter than the foreground",
                        )

    def test_dark_ansi_colors_stay_legible_without_glaring(self):
        palette = load_palette("macos-classic-dark")
        for key in self.ANSI_KEYS:
            with self.subTest(key=key):
                ratio = contrast_ratio(palette[key], palette["background"])
                self.assertGreater(ratio, 3.0, f"{key} is too dim to read")
                self.assertLess(ratio, 12.0, f"{key} glares against the surface")

    def test_bright_variants_stay_brighter_than_their_base(self):
        for name in VARIANTS:
            palette = load_palette(name)
            for key in ("red", "yellow", "green", "cyan", "blue", "magenta"):
                with self.subTest(name=name, key=key):
                    base = relative_luminance(palette[key])
                    bright = relative_luminance(palette[f"bright_{key}"])
                    if palette["mode"] == "dark":
                        self.assertGreater(bright, base, f"bright_{key} must outrank {key}")

    def test_dark_terminal_palette_tones_down_the_neon_source_colors(self):
        palette = load_palette("macos-classic-dark")
        self.assertEqual("#0AC2A2", palette["cyan"])
        self.assertEqual("#5CDBC6", palette["bright_cyan"])
        self.assertEqual("#CC9E00", palette["yellow"])
        self.assertEqual("#DBBB76", palette["bright_yellow"])


class IntegrationTests(unittest.TestCase):
    REQUIRED_FILES = {
        "hyprland.lua",
        "btop.theme",
        "chromium.theme",
        "icons.theme",
        "vscode.json",
        "zed.json",
        "shell.hyprland.toml",
        "slack.theme",
    }

    def test_all_integration_files_exist(self):
        for name in VARIANTS:
            for filename in self.REQUIRED_FILES:
                with self.subTest(name=name, filename=filename):
                    self.assertTrue((theme_dir(name) /filename).is_file())

    def test_chromium_uses_source_background(self):
        for name, expected in VARIANTS.items():
            with self.subTest(name=name):
                value = (theme_dir(name) /"chromium.theme").read_text().strip()
                self.assertEqual(expected["chromium"], value)
                self.assertTrue(all(0 <= int(channel) <= 255 for channel in value.split(",")))

    def test_slack_legacy_themes_are_importable_and_readable(self):
        for name in VARIANTS:
            with self.subTest(name=name):
                colors = (theme_dir(name) / "slack.theme").read_text().strip().split(",")
                self.assertEqual(8, len(colors))
                self.assertTrue(all(re.fullmatch(r"#[0-9A-Fa-f]{6}", color) for color in colors))

                column_background, _, active_item, active_item_text, _, text, _, badge = colors
                self.assertGreaterEqual(contrast_ratio(text, column_background), 4.5)
                self.assertGreaterEqual(contrast_ratio(active_item_text, active_item), 4.5)
                self.assertGreaterEqual(contrast_ratio("#FFFFFF", badge), 4.5)

    def test_hyprland_active_border_is_quieter_than_accent(self):
        for name in VARIANTS:
            with self.subTest(name=name):
                content = (theme_dir(name) /"hyprland.lua").read_text()
                border = "#" + content.split('rgb(', 1)[1].split(')', 1)[0]
                palette = load_palette(name)
                self.assertNotEqual(border.lower(), palette["accent"].lower())
                # Quiet means less contrast against its own background than the
                # accent would carry -- luminance alone flips between modes.
                self.assertLess(
                    contrast_ratio(border, palette["background"]),
                    contrast_ratio(palette["accent"], palette["background"]),
                )
                self.assertIn("border_active = active_border_color", content)

    def test_active_borders_are_neutral_never_blue(self):
        expected = {
            "macos-classic-light": {"window": "d2d2d2", "panel": "#B0B0B0"},
            "macos-classic-dark": {"window": "595959", "panel": "#595959"},
        }
        for name, borders in expected.items():
            with self.subTest(name=name):
                content = (theme_dir(name) /"hyprland.lua").read_text().lower()
                self.assertIn(f'active_border_color = "rgb({borders["window"]})"', content)
                self.assertTrue(is_neutral("#" + borders["window"]))

                shell = tomllib.loads((theme_dir(name) /"shell.hyprland.toml").read_text())
                self.assertEqual(borders["panel"], shell["active-border"])
                for key in ("active-border", "active-border-foreground"):
                    self.assertTrue(
                        is_neutral(shell[key]),
                        f"{name} {key} must be neutral, got {shell[key]}",
                    )

    def test_dark_border_hierarchy_matches_the_source_surfaces(self):
        content = (theme_dir("macos-classic-dark") / "hyprland.lua").read_text().lower()
        self.assertIn('inactive_border_color = "rgb(202020)"', content)
        self.assertIn("inactive_border = inactive_border_color", content)
        self.assertIn("border_inactive = inactive_border_color", content)
        code = [line for line in content.splitlines() if not line.strip().startswith("--")]
        self.assertNotIn("rgba(", "\n".join(code), "borders must not be alpha-dimmed")

        palette = load_palette("macos-classic-dark")
        active = "#595959"
        inactive = "#202020"
        shell = tomllib.loads(
            (theme_dir("macos-classic-dark") / "shell.hyprland.toml").read_text()
        )
        self.assertEqual(active, shell["active-border"])
        self.assertEqual(
            active,
            shell["active-border-foreground"],
            "shell menus must use the same border as the focused window",
        )
        self.assertEqual(inactive, shell["inactive-border"])
        self.assertGreater(
            contrast_ratio(active, palette["background"]),
            contrast_ratio(inactive, palette["background"]),
            "the focused window must read brighter than an unfocused one",
        )
        self.assertGreater(
            relative_luminance(active),
            relative_luminance(inactive),
            "the focused window must read brighter than an unfocused one",
        )

    def test_btop_defines_all_required_theme_fields(self):
        required = {
            "main_bg", "main_fg", "title", "hi_fg", "selected_bg", "selected_fg",
            "inactive_fg", "proc_misc", "cpu_box", "mem_box", "net_box", "proc_box",
            "div_line", "graph_text", "meter_bg", "process_start", "process_mid",
            "process_end", "temp_start", "temp_mid", "temp_end", "cpu_start", "cpu_mid",
            "cpu_end", "free_start", "free_mid", "free_end", "cached_start", "cached_mid",
            "cached_end", "available_start", "available_mid", "available_end", "used_start",
            "used_mid", "used_end", "download_start", "download_mid", "download_end",
            "upload_start", "upload_mid", "upload_end",
        } | {f"gradient_color_{index}" for index in range(8)}
        for name in VARIANTS:
            with self.subTest(name=name):
                content = (theme_dir(name) /"btop.theme").read_text()
                present = {
                    line.split("]", 1)[0].removeprefix("theme[")
                    for line in content.splitlines()
                    if line.startswith("theme[")
                }
                self.assertEqual(required, present)

    def test_btop_covers_every_key_current_omarchy_would_generate(self):
        # Shipping btop.theme replaces Omarchy's generated file wholesale, so any
        # key we omit silently falls back to btop's own defaults.
        template = Path("/usr/share/omarchy/default/themed/btop.theme.tpl")
        if not template.is_file():
            self.skipTest("Omarchy is not installed")
        generated = set(re.findall(r"theme\[(\w+)\]", template.read_text()))
        for name in VARIANTS:
            with self.subTest(name=name):
                ours = set(re.findall(r"theme\[(\w+)\]", (theme_dir(name) /"btop.theme").read_text()))
                self.assertEqual(generated, ours)

    def test_btop_graph_gradient_ramps_from_background_to_foreground(self):
        for name in VARIANTS:
            with self.subTest(name=name):
                content = (theme_dir(name) /"btop.theme").read_text()
                ramp = [
                    re.search(rf'theme\[gradient_color_{index}\]="(#\w{{6}})"', content).group(1)
                    for index in range(8)
                ]
                palette = load_palette(name)
                self.assertEqual(palette["background"].upper(), ramp[0].upper())
                steps = [relative_luminance(color) for color in ramp]
                if palette["mode"] == "dark":
                    self.assertEqual(steps, sorted(steps), f"{name} ramp must brighten")
                else:
                    self.assertEqual(steps, sorted(steps, reverse=True), f"{name} ramp must darken")
                self.assertEqual(len(set(ramp)), len(ramp), "ramp steps must be distinct")

    def test_editor_metadata_is_valid(self):
        for name, expected in VARIANTS.items():
            with self.subTest(name=name):
                metadata = json.loads((theme_dir(name) /"vscode.json").read_text())
                self.assertEqual({"name", "extension"}, set(metadata))
                self.assertEqual(expected["vscode"], metadata["name"])
                self.assertEqual("huacnlee.theme-macos-classic", metadata["extension"])

                zed = json.loads((theme_dir(name) /"zed.json").read_text())
                self.assertEqual(
                    {
                        "extension": "macos-classic",
                        "name": expected["zed"],
                    },
                    zed,
                )

    def test_icon_theme_is_installed(self):
        icon_roots = (Path("/usr/share/icons"), Path.home() / ".local/share/icons", Path.home() / ".icons")
        for name in VARIANTS:
            with self.subTest(name=name):
                icon_theme = (theme_dir(name) /"icons.theme").read_text().strip()
                self.assertTrue(
                    any((root / icon_theme / "index.theme").is_file() for root in icon_roots),
                    f"Icon theme {icon_theme!r} is not installed",
                )

    @unittest.skipUnless(shutil.which("omarchy-theme-set"), "Omarchy is not installed")
    def test_current_omarchy_generates_palette_native_neovim_theme(self):
        for name, expected in VARIANTS.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                home = Path(temporary)
                theme_root = home / ".config/omarchy/themes"
                runtime = home / "run"
                theme_root.mkdir(parents=True)
                runtime.mkdir()
                # Copy the directory wholesale, the way `omarchy theme install`
                # clones a repo: for the dark variant that drags the README,
                # installer, and light variant in alongside the theme files, and
                # omarchy still has to resolve the right palette and background.
                installed = INSTALLED_NAMES[name]
                shutil.copytree(
                    theme_dir(name),
                    theme_root / installed,
                    ignore=shutil.ignore_patterns(".git", "__pycache__"),
                )
                env = os.environ | {
                    "HOME": str(home),
                    "OMARCHY_PATH": "/usr/share/omarchy",
                    "OMARCHY_THEME_HEADLESS": "1",
                    "XDG_RUNTIME_DIR": str(runtime),
                }
                result = subprocess.run(
                    ["omarchy-theme-set", installed], capture_output=True, text=True, env=env
                )
                self.assertEqual(0, result.returncode, result.stderr)
                generated = (home / ".local/state/omarchy/current/theme/neovim.lua").read_text()
                self.assertIn('"bjarneo/aether.nvim"', generated)
                self.assertIn(expected["background"], generated)

                shell = tomllib.loads((home / ".local/state/omarchy/current/theme/shell.toml").read_text())
                panel_border = shell["hyprland"]["active-border"]
                self.assertTrue(
                    is_neutral(panel_border),
                    f"{name} panel border must be neutral, got {panel_border}",
                )
                self.assertLess(
                    contrast_ratio(panel_border, expected["background"]),
                    contrast_ratio(expected["accent"], expected["background"]),
                )

    @unittest.skipUnless(shutil.which("luac"), "luac is not installed")
    def test_lua_files_parse(self):
        for name in VARIANTS:
            for filename in ("hyprland.lua",):
                with self.subTest(name=name, filename=filename):
                    result = subprocess.run(
                        ["luac", "-p", theme_dir(name) /filename],
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(0, result.returncode, result.stderr)


class LongbridgeThemeTests(unittest.TestCase):
    def load(self):
        return json.loads((ROOT / "longbridge.json").read_text())

    def theme(self):
        return self.load()["themes"][0]

    def test_the_file_carries_one_dark_theme_named_after_omarchy(self):
        theme_set = self.load()
        self.assertEqual(LONGBRIDGE_THEME, theme_set["name"])
        # Longbridge stores themes in a map keyed by theme name, so two variants
        # sharing a name would silently drop one. This file ships dark only.
        self.assertEqual(1, len(theme_set["themes"]))
        self.assertEqual(LONGBRIDGE_THEME, self.theme()["name"])
        self.assertEqual("dark", self.theme()["mode"])

    def test_colors_come_from_the_dark_palette(self):
        palette = load_palette("macos-classic-dark")
        colors = self.theme()["colors"]
        expected = {
            "accent.background": palette["lighter_background"],
            "accent.foreground": palette["light_foreground"],
            "background": palette["background"],
            "foreground": palette["foreground"],
            "link": palette["blue"],
            "list.active.border": palette["accent"],
            "list.even.background": palette["lighter_background"],
            "muted.background": palette["lighter_background"],
            "muted.foreground": palette["muted"],
            "popover.background": palette["dark_background"],
            "popover.foreground": palette["light_foreground"],
            "primary.background": palette["accent"],
            "primary.foreground": palette["bright_foreground"],
            "ring": palette["accent"],
            "selection.background": palette["selection"],
            "tab.active.background": palette["background"],
            "tab.foreground": palette["dark_foreground"],
            "tab_bar.background": palette["darker_background"],
            "title_bar.background": palette["dark_background"],
            "base.blue": palette["blue"],
            "base.cyan": palette["cyan"],
            "base.green": palette["green"],
            "base.magenta": palette["magenta"],
            "base.red": palette["red"],
            "base.yellow": palette["yellow"],
        }
        for key, value in expected.items():
            with self.subTest(key=key):
                self.assertEqual(value.lower(), colors[key].lower())

    def test_translucent_colors_are_tinted_by_the_surface_they_sit_on(self):
        palette = load_palette("macos-classic-dark")
        colors = self.theme()["colors"]
        self.assertEqual(
            palette["background"].lower() + "00", colors["scrollbar.background"].lower()
        )
        self.assertTrue(
            colors["list.active.background"].lower().startswith(palette["accent"].lower()),
            "a selected row is the accent dimmed, not a separate color",
        )

    def test_every_color_is_a_hex_value(self):
        for key, value in self.theme()["colors"].items():
            with self.subTest(key=key):
                self.assertRegex(value, r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")

    def test_text_stays_readable_on_its_own_surface(self):
        colors = self.theme()["colors"]
        pairs = [
            ("foreground", "background"),
            ("accent.foreground", "accent.background"),
            ("popover.foreground", "popover.background"),
            ("secondary.foreground", "secondary.background"),
            ("muted.foreground", "muted.background"),
            ("tab.foreground", "tab_bar.background"),
        ]
        for foreground, background in pairs:
            with self.subTest(foreground=foreground):
                self.assertGreaterEqual(
                    contrast_ratio(colors[foreground], colors[background]), 4.5
                )
        # The accent pair comes straight from colors.toml, and white-on-blue
        # lands at 3.7. Buttons are UI components, so 3:1 is the bar they meet.
        self.assertGreaterEqual(
            contrast_ratio(colors["primary.foreground"], colors["primary.background"]), 3.0
        )

    def test_the_theme_is_not_part_of_the_omarchy_theme_itself(self):
        # Omarchy never reads this file; only install.sh does. Keeping it out of
        # THEME_FILES keeps it out of ~/.config/omarchy/themes.
        self.assertNotIn("longbridge.json", THEME_FILES)
        self.assertFalse((theme_dir("macos-classic-light") / "longbridge.json").exists())


class AssetTests(unittest.TestCase):
    def png_dimensions(self, path):
        data = path.read_bytes()
        self.assertEqual(b"\x89PNG\r\n\x1a\n", data[:8])
        self.assertEqual(b"IHDR", data[12:16])
        return struct.unpack(">II", data[16:24])

    def png_scanlines(self, path):
        data = path.read_bytes()
        offset = 8
        compressed = []
        while offset < len(data):
            length = struct.unpack(">I", data[offset : offset + 4])[0]
            kind = data[offset + 4 : offset + 8]
            payload = data[offset + 8 : offset + 8 + length]
            if kind == b"IDAT":
                compressed.append(payload)
            offset += 12 + length
        pixels = zlib.decompress(b"".join(compressed))
        width, height = self.png_dimensions(path)
        row_size = 1 + width * 3
        return [pixels[index * row_size : (index + 1) * row_size] for index in range(height)]

    def png_corner_rgb(self, path, bottom=False):
        rows = self.png_scanlines(path)
        row = rows[-1] if bottom else rows[0]
        self.assertEqual(0, row[0], "Generated PNG must use filter type 0")
        return tuple(row[1:4])

    def png_pixels(self, path):
        for row in self.png_scanlines(path):
            self.assertEqual(0, row[0], "Generated PNG must use filter type 0")
            body = row[1:]
            for index in range(0, len(body), 3):
                yield tuple(body[index : index + 3])

    def test_assets_have_expected_png_dimensions(self):
        for name in VARIANTS:
            expected = {
                theme_dir(name) /"backgrounds" / f"{name}.png": (1920, 1080),
                theme_dir(name) /"unlock.png": (1920, 1080),
                theme_dir(name) /"preview.png": (640, 360),
                theme_dir(name) /"preview-unlock.png": (640, 360),
            }
            for path, dimensions in expected.items():
                with self.subTest(path=path):
                    self.assertEqual(dimensions, self.png_dimensions(path))

    def test_wallpaper_is_a_flat_fill_with_no_artwork(self):
        # There is no wallpaper image on purpose: every pixel is the same color,
        # so the desktop is blank rather than a picture.
        expected = {
            "macos-classic-light": (216, 216, 216),
            "macos-classic-dark": (19, 19, 19),
        }
        for name, fill in expected.items():
            with self.subTest(name=name):
                path = theme_dir(name) / "backgrounds" / f"{name}.png"
                self.assertEqual(fill, self.png_corner_rgb(path))
                self.assertEqual(fill, self.png_corner_rgb(path, bottom=True))
                self.assertEqual({fill}, set(self.png_pixels(path)))

    def test_dark_assets_use_the_source_editor_background(self):
        theme = theme_dir("macos-classic-dark")
        paths = (
            theme / "backgrounds/macos-classic-dark.png",
            theme / "unlock.png",
            theme / "preview.png",
            theme / "preview-unlock.png",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual((19, 19, 19), self.png_corner_rgb(path))
                self.assertEqual((19, 19, 19), self.png_corner_rgb(path, bottom=True))

    def test_light_wallpaper_sits_below_every_palette_surface(self):
        # The desktop is darker than the editor background so windows read as
        # raised off it instead of melting into it.
        name = "macos-classic-light"
        path = theme_dir(name) / "backgrounds" / f"{name}.png"
        fill = "#%02X%02X%02X" % self.png_corner_rgb(path)
        palette = load_palette(name)
        for key in ("background", "dark_background", "darker_background", "lighter_background"):
            self.assertLess(
                relative_luminance(fill),
                relative_luminance(palette[key]),
                f"{name} wallpaper must be darker than {key}",
            )


class InstallerTests(unittest.TestCase):
    def run_installer(self, *arguments, env=None, activate=False):
        # Activation is the installer's default, so every test that is not about
        # activation opts out: otherwise the suite would switch the theme of the
        # machine it runs on. Both destinations default to somewhere under $HOME,
        # so point HOME at a throwaway directory for the same reason.
        prefix = () if activate else ("--no-activate",)
        with tempfile.TemporaryDirectory() as home:
            return subprocess.run(
                ["bash", ROOT / "install.sh", *prefix, *map(str, arguments)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env=(env or os.environ) | {"HOME": home},
            )

    def stub_omarchy(self, temporary):
        bin_dir = Path(temporary) / "bin"
        bin_dir.mkdir()
        log = Path(temporary) / "omarchy-invoked"
        omarchy = bin_dir / "omarchy"
        omarchy.write_text(f'#!/usr/bin/env bash\necho "$@" >> {log}\n')
        omarchy.chmod(0o755)
        return log, os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"}

    def test_installer_applies_the_dark_variant_by_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "themes"
            log, env = self.stub_omarchy(temporary)

            result = self.run_installer("--destination", destination, env=env, activate=True)
            self.assertEqual(0, result.returncode, result.stderr)
            for installed in INSTALLED_NAMES.values():
                self.assertTrue((destination / installed / "colors.toml").is_file())
            self.assertEqual("theme set macos-classic", log.read_text().strip())
            self.assertIn("Monaco", result.stdout)

    def test_no_activate_installs_without_switching_theme(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "themes"
            log, env = self.stub_omarchy(temporary)

            result = self.run_installer("--destination", destination, env=env)
            self.assertEqual(0, result.returncode, result.stderr)
            for installed in INSTALLED_NAMES.values():
                self.assertTrue((destination / installed / "colors.toml").is_file())
            self.assertFalse(log.exists())

    def test_installer_copies_only_theme_files_out_of_the_repository_root(self):
        # The dark variant shares its directory with the README, installer, and
        # the light variant; none of that belongs in an installed theme.
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "themes"
            self.assertEqual(0, self.run_installer("--destination", destination).returncode)
            installed = destination / INSTALLED_NAMES["macos-classic-dark"]
            self.assertEqual(THEME_FILES, {entry.name for entry in installed.iterdir()})

    def test_installer_updates_both_existing_themes_by_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "themes"
            self.assertEqual(0, self.run_installer("--destination", destination).returncode)
            markers = [
                destination / installed / "keep-me" for installed in INSTALLED_NAMES.values()
            ]
            for marker in markers:
                marker.write_text("old")

            result = self.run_installer("--destination", destination)
            self.assertEqual(0, result.returncode, result.stderr)
            for marker in markers:
                self.assertFalse(marker.exists())

    def test_installer_copies_the_longbridge_theme_where_the_app_reads_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "themes"
            longbridge = Path(temporary) / ".longbridge"
            longbridge.mkdir()

            result = self.run_installer(
                "--destination",
                destination,
                "--longbridge-destination",
                longbridge / "themes",
            )
            self.assertEqual(0, result.returncode, result.stderr)

            installed = longbridge / "themes" / LONGBRIDGE_INSTALLED_NAME
            self.assertEqual(
                json.loads((ROOT / "longbridge.json").read_text()),
                json.loads(installed.read_text()),
            )
            self.assertIn("Longbridge", result.stdout)

    def test_installer_leaves_a_machine_without_longbridge_alone(self):
        # ~/.longbridge appears the first time the app runs. Without it there is
        # nothing to theme, and the installer must not conjure the directory.
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "themes"
            longbridge = Path(temporary) / "absent" / "themes"

            result = self.run_installer(
                "--destination", destination, "--longbridge-destination", longbridge
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse(longbridge.parent.exists())
            self.assertIn("Longbridge is not set up", result.stdout)

    def test_unknown_argument_fails_without_copying(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "themes"
            result = self.run_installer("--unknown", "--destination", destination)
            self.assertNotEqual(0, result.returncode)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
