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


class AssetTests(unittest.TestCase):
    def png_dimensions(self, path):
        data = path.read_bytes()
        self.assertEqual(b"\x89PNG\r\n\x1a\n", data[:8])
        self.assertEqual(b"IHDR", data[12:16])
        return struct.unpack(">II", data[16:24])

    def png_corner_rgb(self, path, bottom=False):
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
        row = pixels[(height - 1) * row_size :] if bottom else pixels
        self.assertEqual(0, row[0], "Generated PNG must use filter type 0")
        return tuple(row[1:4])

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

    def test_wallpaper_starts_with_neutral_surface_not_blue_accent(self):
        expected = {
            "macos-classic-light": (255, 255, 255),
            "macos-classic-dark": (19, 19, 19),
        }
        for name, top_rgb in expected.items():
            with self.subTest(name=name):
                path = theme_dir(name) /"backgrounds" / f"{name}.png"
                self.assertEqual(top_rgb, self.png_corner_rgb(path))

    def test_light_wallpaper_ends_on_the_primary_surface(self):
        path = theme_dir("macos-classic-light") / "backgrounds/macos-classic-light.png"
        self.assertEqual((249, 249, 249), self.png_corner_rgb(path, bottom=True))

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


class InstallerTests(unittest.TestCase):
    def run_installer(self, *arguments, env=None):
        return subprocess.run(
            ["bash", ROOT / "install.sh", *map(str, arguments)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_installer_copies_both_themes_without_activating_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "themes"
            bin_dir = Path(temporary) / "bin"
            bin_dir.mkdir()
            marker = Path(temporary) / "omarchy-invoked"
            omarchy = bin_dir / "omarchy"
            omarchy.write_text(f"#!/usr/bin/env bash\ntouch {marker}\n")
            omarchy.chmod(0o755)
            env = os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"}

            result = self.run_installer("--destination", destination, env=env)
            self.assertEqual(0, result.returncode, result.stderr)
            for installed in INSTALLED_NAMES.values():
                self.assertTrue((destination / installed / "colors.toml").is_file())
            self.assertFalse(marker.exists())
            self.assertIn("Monaco", result.stdout)

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

    def test_unknown_argument_fails_without_copying(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "themes"
            result = self.run_installer("--unknown", "--destination", destination)
            self.assertNotEqual(0, result.returncode)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
