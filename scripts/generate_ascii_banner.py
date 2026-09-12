import os
import math
from PIL import Image, ImageDraw, ImageFont

def generate_production_banner():
    output_dir = "assets"
    os.makedirs(output_dir, exist_ok=True)
    gif_path = os.path.join(output_dir, "banner-ascii.gif")
    
    # Dimensions (Strict 8-pt rhythm)
    WIDTH = 920
    HEIGHT = 356
    PAD_X = 20
    PAD_Y = 16
    
    # Palette definition (Disciplined, high-contrast, GitHub-native dark theme)
    BG_CANVAS = (13, 17, 23)        # #0d1117 (GitHub Canvas Dark)
    TERMINAL_BG = (15, 23, 42)      # #0f172a (Deep Slate 900)
    BORDER_COLOR = (51, 65, 85)     # #334155 (Slate 700)
    HEADER_BG = (30, 41, 59)        # #1e293b (Slate 800)
    
    # High-contrast, intentional accent palette
    COLOR_LOGO_BASE = (56, 189, 248)    # #38bdf8 (Sky Cyan)
    COLOR_LOGO_HIGHLIGHT = (129, 140, 248) # #818cf8 (Indigo accent)
    COLOR_TEXT_WHITE = (241, 245, 249)  # #f1f5f9 (Slate 100)
    COLOR_MUTED = (148, 163, 184)       # #94a3b8 (Slate 400)
    COLOR_EMERALD = (74, 222, 128)      # #4ade80 (Emerald active)
    COLOR_AMBER = (251, 191, 36)        # #fbbf24 (Amber warning)
    COLOR_CORAL = (251, 146, 60)        # #fb923c (Coral Orange)
    COLOR_VIOLET = (192, 132, 252)      # #c084fc (Purple legend)
    
    # Water palette
    COLOR_WATER_WAVE = (45, 80, 110)    # Active wave
    COLOR_WATER_DEEP = (24, 42, 60)     # Background wave
    
    # Fonts
    font_path = "C:/Windows/Fonts/consola.ttf"
    font_bold_path = "C:/Windows/Fonts/consolab.ttf"
    
    font = ImageFont.truetype(font_path, 14)
    font_bold = ImageFont.truetype(font_bold_path, 14)
    font_small = ImageFont.truetype(font_path, 11)
    font_small_bold = ImageFont.truetype(font_bold_path, 11)
    
    char_w = 7.7
    char_h = 19
    
    # 40 frames @ 16 FPS = 2.5s smooth loop
    num_frames = 40
    fps = 16
    duration_ms = int(1000 / fps)
    
    # ASCII Logo lines
    logo_lines = [
        "███████╗██╗███████╗██╗  ██╗███████╗██████╗ ",
        "██╔════╝██║██╔════╝██║  ██║██╔════╝██╔══██╗",
        "█████╗  ██║███████╗███████║█████╗  ██████╔╝",
        "██╔══╝  ██║╚════██║██╔══██║██╔══╝  ██╔══██╗",
        "██║     ██║███████║██║  ██║███████╗██║  ██║",
        "╚═╝     ╚═╝╚══════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝"
    ]
    
    side_specs = [
        ("AUTONOMOUS RL AGENT", COLOR_TEXT_WHITE, True),
        ("Stardew Valley Sim-to-Real Transfer", COLOR_MUTED, False),
        ("PPO Policy: Converged (89.2% Catch Rate)", COLOR_EMERALD, False),
        ("Loop: 30 Hz (Δt ≈ 33.3 ms) | p99 < 0.05 ms", COLOR_AMBER, False),
        ("Capture: DXGI Desktop Duplication (Screen 3)", COLOR_LOGO_BASE, False),
        ("Failsafe: F9 Emergency Killswitch Armed", (248, 113, 113), False)
    ]
    
    RIVER_COLS = 110
    
    # Staggered fish schools with smooth entering/exiting
    # (sprite, color, row, speed_chars_per_frame, start_offset)
    fish_list = [
        ("><(((°>", COLOR_CORAL, 0, 2.75, 12),          # Row 0: Salmon
        ("<°)))><", COLOR_LOGO_BASE, 1, -2.75, 95),     # Row 1: Catfish
        ("><>   ><>", COLOR_AMBER, 2, 3.25, 30),        # Row 2: Minnow pair
        ("<*(((><", COLOR_VIOLET, 3, -1.75, 80)         # Row 3: Legend fish
    ]
    
    frames_rgb = []
    
    for f in range(num_frames):
        img = Image.new("RGB", (WIDTH, HEIGHT), BG_CANVAS)
        draw = ImageDraw.Draw(img)
        
        # 1. Terminal Window Frame
        frame_box = [PAD_X, PAD_Y, WIDTH - PAD_X, HEIGHT - PAD_Y]
        draw.rounded_rectangle(frame_box, radius=8, fill=TERMINAL_BG, outline=BORDER_COLOR, width=1)
        
        # 2. Terminal Title Header
        header_h = 34
        header_box = [PAD_X, PAD_Y, WIDTH - PAD_X, PAD_Y + header_h]
        draw.rounded_rectangle(header_box, radius=8, fill=HEADER_BG)
        draw.rectangle([PAD_X, PAD_Y + 16, WIDTH - PAD_X, PAD_Y + header_h], fill=HEADER_BG)
        draw.line([PAD_X, PAD_Y + header_h, WIDTH - PAD_X, PAD_Y + header_h], fill=BORDER_COLOR, width=1)
        
        # Window controls
        draw.ellipse([PAD_X + 14, PAD_Y + 12, PAD_X + 24, PAD_Y + 22], fill=(239, 68, 68))
        draw.ellipse([PAD_X + 32, PAD_Y + 12, PAD_X + 42, PAD_Y + 22], fill=(245, 158, 11))
        draw.ellipse([PAD_X + 50, PAD_Y + 12, PAD_X + 60, PAD_Y + 22], fill=(34, 197, 94))
        
        # Header title
        draw.text((PAD_X + 76, PAD_Y + 10), "fisher@runtime — zsh — 110x18", font=font_small, fill=COLOR_MUTED)
        
        # Active badge
        pulse = (f // 5) % 2
        active_color = COLOR_EMERALD if pulse == 0 else (34, 197, 94)
        draw.text((WIDTH - PAD_X - 130, PAD_Y + 10), "● 30 Hz ACTIVE", font=font_small_bold, fill=active_color)
        
        # Content bounds
        content_x = PAD_X + 20
        content_w = (WIDTH - PAD_X * 2) - 40
        cur_y = PAD_Y + header_h + 14
        
        # 3. Section 1: Logo + Specs
        for i, l_text in enumerate(logo_lines):
            shimmer_offset = (f + i * 2) % num_frames
            shimmer_ratio = shimmer_offset / num_frames
            r = int(COLOR_LOGO_BASE[0] * (1 - shimmer_ratio * 0.35) + COLOR_LOGO_HIGHLIGHT[0] * (shimmer_ratio * 0.35))
            g = int(COLOR_LOGO_BASE[1] * (1 - shimmer_ratio * 0.35) + COLOR_LOGO_HIGHLIGHT[1] * (shimmer_ratio * 0.35))
            b = int(COLOR_LOGO_BASE[2] * (1 - shimmer_ratio * 0.35) + COLOR_LOGO_HIGHLIGHT[2] * (shimmer_ratio * 0.35))
            
            draw.text((content_x, cur_y + i * char_h), l_text, font=font_bold, fill=(r, g, b))
            
            s_text, s_col, is_bold = side_specs[i]
            s_font = font_bold if is_bold else font
            draw.text((content_x + 380, cur_y + i * char_h), f"// {s_text}", font=s_font, fill=s_col)
            
        cur_y += len(logo_lines) * char_h + 12
        draw.line([content_x, cur_y, content_x + content_w, cur_y], fill=BORDER_COLOR, width=1)
        cur_y += 12
        
        # 4. Section 2: River ASCII Current & Fish School
        river_grid = []
        for r_idx in range(4):
            row_chars = []
            wave_shift = (f * 2 + r_idx * 5) % 8
            for c in range(RIVER_COLS):
                phase = (c + wave_shift) % 8
                if phase == 0:
                    row_chars.append(("~", "wave"))
                elif phase == 2:
                    row_chars.append(("^", "wave"))
                elif phase == 4:
                    row_chars.append(("~", "wave"))
                elif phase == 6:
                    if (c * 7 + f) % 17 == 0:
                        row_chars.append(("o", "bubble"))
                    elif (c * 11 + f) % 23 == 0:
                        row_chars.append(("°", "bubble"))
                    else:
                        row_chars.append((".", "deep"))
                else:
                    row_chars.append((" ", "space"))
            river_grid.append(row_chars)
            
        # Seamless entry/exit without clipping
        for sprite, fish_color, row_idx, speed, start_c in fish_list:
            sprite_len = len(sprite)
            total_range = RIVER_COLS + sprite_len * 2
            raw_pos = (start_c + speed * f) % total_range - sprite_len
            base_col = int(raw_pos)
            
            for k in range(sprite_len):
                col_idx = base_col + k
                if 0 <= col_idx < RIVER_COLS:
                    river_grid[row_idx][col_idx] = (sprite[k], fish_color)
                    
        for r_idx, row in enumerate(river_grid):
            line_y = cur_y + r_idx * char_h
            for c_idx, cell in enumerate(row):
                ch, style = cell
                cell_x = content_x + int(c_idx * char_w)
                if isinstance(style, tuple):
                    draw.text((cell_x, line_y), ch, font=font_bold, fill=style)
                elif style == "wave":
                    draw.text((cell_x, line_y), ch, font=font, fill=COLOR_WATER_WAVE)
                elif style == "deep":
                    draw.text((cell_x, line_y), ch, font=font, fill=COLOR_WATER_DEEP)
                elif style == "bubble":
                    draw.text((cell_x, line_y), ch, font=font, fill=(96, 165, 250))
                    
        cur_y += 4 * char_h + 12
        draw.line([content_x, cur_y, content_x + content_w, cur_y], fill=BORDER_COLOR, width=1)
        cur_y += 10
        
        # 5. Section 3: Telemetry Footer Status
        dot_count = (f // 4) % 4
        dots_str = "." * dot_count + " " * (3 - dot_count)
        
        footer_col1 = f"SYSTEM: ONLINE {dots_str}"
        footer_col2 = "SIM-TO-REAL: PASS [42/42]"
        footer_col3 = "FAILSAFE: [F9] ARMED"
        
        draw.text((content_x, cur_y), footer_col1, font=font_bold, fill=COLOR_EMERALD)
        
        # Exactly center the middle telemetry string
        bbox_mid = font_bold.getbbox(footer_col2)
        mid_w = bbox_mid[2] - bbox_mid[0]
        mid_x = content_x + (content_w - mid_w) // 2
        draw.text((mid_x, cur_y), footer_col2, font=font_bold, fill=COLOR_TEXT_WHITE)
        
        # Right align failsafe
        bbox_right = font_bold.getbbox(footer_col3)
        right_w = bbox_right[2] - bbox_right[0]
        draw.text((content_x + content_w - right_w, cur_y), footer_col3, font=font_bold, fill=COLOR_AMBER)
        
        frames_rgb.append(img)
        
    print(f"Quantizing {len(frames_rgb)} frames using unified 64-color palette...")
    palette_sample = frames_rgb[0].quantize(colors=64, method=Image.Quantize.MEDIANCUT)
    frames_p = [frame.quantize(palette=palette_sample, dither=Image.Dither.NONE) for frame in frames_rgb]
    
    frames_p[0].save(
        gif_path,
        save_all=True,
        append_images=frames_p[1:],
        duration=duration_ms,
        loop=0,
        optimize=True,
        disposal=2
    )
    
    size_kb = os.path.getsize(gif_path) / 1024
    print(f"Refined GIF generated: {gif_path} ({size_kb:.1f} KB)")
    return gif_path

if __name__ == "__main__":
    generate_production_banner()
