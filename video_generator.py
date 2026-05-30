"""
프로페셔널 숏폼 영상 생성기 (FFmpeg + PIL + Librosa)
Instagram Reels / YouTube Shorts 품질의 광고 영상을 이미지로부터 생성합니다.
배경음악(BGM)의 비트 리듬을 분석하여 화면 전환을 동기화하고 타이핑 자막 효과를 적용합니다.
"""
import os
import subprocess
import time
import textwrap
import shutil
import random
import glob
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from typing import List, Optional

# === Constants ===
SHORTS_W, SHORTS_H = 1080, 1920
FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
FONT_BOLD = os.path.join(FONT_DIR, "Pretendard-ExtraBold.otf")
FONT_FALLBACK = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

# Ken Burns 프리셋 (이미지마다 다른 움직임)
KB_PRESETS = [
    # zoom_expr, x_expr, y_expr  ('{F}'는 총 프레임 수로 치환됨)
    ("min(1+0.15*on/{F},1.15)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),                      # 중앙 줌인
    ("min(1+0.12*on/{F},1.12)", "iw/2-(iw/zoom/2)+on/{F}*60", "ih/2-(ih/zoom/2)"),             # 우측 줌인
    ("1.12", "(iw-iw/zoom)*(1-on/{F})", "ih/3-(ih/zoom/3)"),                                   # 좌로 패닝
    ("1.15-0.15*on/{F}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),                              # 줌아웃
    ("min(1+0.12*on/{F},1.12)", "iw/2-(iw/zoom/2)-on/{F}*60", "ih/2-(ih/zoom/2)+on/{F}*20"),   # 좌측 줌인
]


def _get_font(size: int) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD if os.path.exists(FONT_BOLD) else FONT_FALLBACK
    return ImageFont.truetype(path, size)


def _preprocess_image(img_path: str, out_path: str):
    """이미지를 1080x1920(9:16)으로 리사이즈+크롭합니다."""
    img = Image.open(img_path).convert("RGB")
    # cover crop: 비율 유지하며 꽉 채운 뒤 중앙 크롭
    target_ratio = SHORTS_W / SHORTS_H
    img_ratio = img.width / img.height
    if img_ratio > target_ratio:
        new_h = img.height
        new_w = int(new_h * target_ratio)
    else:
        new_w = img.width
        new_h = int(new_w / target_ratio)
    left = (img.width - new_w) // 2
    top = (img.height - new_h) // 2
    img = img.crop((left, top, left + new_w, top + new_h))
    img = img.resize((SHORTS_W, SHORTS_H), Image.LANCZOS)
    img.save(out_path, "PNG")


def _analyze_bgm_beats(
    bgm_path: Optional[str],
    num_segments: int,
    min_interval: float = 1.8,
    fallback_interval: float = 2.5
) -> List[float]:
    """
    BGM에서 비트(Beat) 위치를 분석하고, 비트 타이밍에 맞춘 전환 타임스탬프 리스트를 반환합니다.
    num_segments = N일 때, N-1개의 전환 타임스탬프가 필요합니다.
    """
    if num_segments <= 1:
        return []

    # 기본 폴백 타임스탬프 목록 (비트 분석 실패 시 사용)
    fallback_times = [round((i + 1) * fallback_interval, 2) for i in range(num_segments - 1)]

    if not bgm_path or not os.path.exists(bgm_path):
        print("No BGM path provided or file does not exist. Using fallback intervals.")
        return fallback_times

    try:
        import librosa
        print(f"Analyzing BGM beats: {bgm_path}...")
        # 메모리와 성능 향상을 위해 낮은 sampling rate(11025Hz)로 로드
        y, sr = librosa.load(bgm_path, sr=11025)
        
        # 템포 및 비트 위치 트래킹
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
        
        if len(beat_times) == 0:
            print("No beats detected. Using fallback intervals.")
            return fallback_times
            
        tempo_float = float(tempo.item()) if hasattr(tempo, "item") else float(tempo)
        print(f"Detected BGM tempo: {tempo_float:.2f} BPM, total beats: {len(beat_times)}")
        
        # 적절한 간격(min_interval 이상)을 가진 비트만 필터링하여 전환 지점 선택
        selected_times = []
        last_t = 0.0
        
        for t in beat_times:
            if t > last_t + min_interval:
                selected_times.append(round(float(t), 2))
                last_t = t
                if len(selected_times) == num_segments - 1:
                    break
                    
        # 만약 비트가 부족하여 전환 지점이 모자란 경우, 마지막 지점부터 fallback 간격으로 채움
        while len(selected_times) < num_segments - 1:
            next_t = last_t + fallback_interval
            selected_times.append(round(next_t, 2))
            last_t = next_t
            
        print(f"Selected beat-synced transition timestamps: {selected_times}")
        return selected_times

    except Exception as e:
        print(f"Error during BGM beat analysis: {e}. Falling back to default intervals.")
        return fallback_times


def _create_typing_overlay_sequence(
    text: str,
    temp_dir: str,
    total_frames: int,
    fps: int = 30,
    chars_per_second: float = 20.0
) -> str:
    """PIL로 한 글자씩 타이핑되는 한글 자막 시퀀스(PNG 프레임들)를 생성하고 파일 패턴을 반환합니다."""
    frames_dir = os.path.join(temp_dir, "typing_frames")
    os.makedirs(frames_dir, exist_ok=True)
    
    # 텍스트 줄바꿈 처리 로직 개선
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    wrapped = []
    # 텍스트 길이에 따라 자동 줄바꿈 너비를 유동적으로 조절
    wrap_width = 14 if len(text) <= 40 else 18
    for line in lines:
        wrapped.extend(textwrap.wrap(line, width=wrap_width))
        
    # 강제로 잘라내는 로직 제거, 모든 텍스트가 표시되도록 함
    
    # 전체 줄을 뉴라인으로 합친 버전
    full_wrapped_text = "\n".join(wrapped)
    total_chars = len(full_wrapped_text)
    
    # 줄 수에 따라 폰트 크기 및 줄 간격을 유동적으로 조정
    total_lines_count = len(wrapped)
    if total_lines_count > 6:
        font_size = 42
        line_h = 60
    elif total_lines_count > 4:
        font_size = 48
        line_h = 68
    else:
        font_size = 58
        line_h = 78
        
    font = _get_font(font_size)
    
    # 텍스트 수직 위치 계산 (텍스트가 길어지면 위로 올라가되, 최소 여백을 보장)
    total_h = total_lines_count * line_h
    base_y = max(100, SHORTS_H - 180 - total_h)
    
    print(f"Generating subtitle typing animation: {total_frames} frames ({chars_per_second} chars/sec)...")
    
    for f in range(total_frames):
        # 현재 프레임에서 표시할 문자 수 계산
        t = f / fps
        num_chars = int(t * chars_per_second)
        
        # 글자 수 상한 제한
        if num_chars > total_chars:
            num_chars = total_chars
            
        frame_text = full_wrapped_text[:num_chars]
        frame_lines = frame_text.split("\n")
        
        # 투명 캔버스 생성
        overlay = Image.new("RGBA", (SHORTS_W, SHORTS_H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        
        # 하단 반투명 그라데이션 배경
        grad_h = 500
        for y in range(grad_h):
            alpha = int(180 * (y / grad_h))
            draw.rectangle(
                [(0, SHORTS_H - grad_h + y), (SHORTS_W, SHORTS_H - grad_h + y + 1)],
                fill=(0, 0, 0, alpha)
            )
            
        # 프레임 텍스트 그리기 (기존 디자인 유지)
        for i, line in enumerate(frame_lines):
            if not line:
                continue
            y = base_y + i * line_h
            bbox = draw.textbbox((0, 0), line, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            x = (SHORTS_W - tw) // 2
            
            # 반투명 검은색 둥근 모서리 박스 추가
            bg_bbox = [x - 15, y - 15, x + tw + 15, y + th + 15]
            draw.rounded_rectangle(bg_bbox, radius=15, fill=(0, 0, 0, 160))
            
            # 그림자 (2px 오프셋)
            draw.text((x + 2, y + 2), line, fill=(0, 0, 0, 200), font=font)
            # 메인 흰색 텍스트
            draw.text((x, y), line, fill=(255, 255, 255, 255), font=font)
            
        # 프레임 저장
        frame_path = os.path.join(frames_dir, f"text_frame_{f:04d}.png")
        overlay.save(frame_path, "PNG")
        
    return os.path.join(frames_dir, "text_frame_%04d.png")


def _create_segment(img_path: str, out_path: str, idx: int, duration: float, fps: int = 30):
    """하나의 이미지에 Ken Burns 효과를 적용한 영상 세그먼트를 생성합니다."""
    frames = int(duration * fps)
    preset = KB_PRESETS[idx % len(KB_PRESETS)]
    z, x, y = [expr.replace("{F}", str(frames)) for expr in preset]

    vf = (f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}"
          f":s={SHORTS_W}x{SHORTS_H}:fps={fps}")

    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", img_path, "-t", str(duration),
           "-vf", vf, "-c:v", "libx264", "-preset", "fast", "-crf", "20",
           "-pix_fmt", "yuv420p", out_path]
    subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True)


def _join_segments(segments: List[str], out_path: str, td: float, segment_durations: List[float]):
    """여러 세그먼트를 hblur, zoomin 등 역동적인 xfade 트랜지션으로 이어붙입니다."""
    if len(segments) == 1:
        shutil.copy(segments[0], out_path)
        return

    inputs = []
    for s in segments:
        inputs.extend(["-i", s])

    # xfade 체인 구성 (hblur, zoomin 등 dynamic transition 사용)
    TRANSITIONS = ["hblur", "zoomin", "fade", "circlecrop"]
    fc_parts = []
    prev = "[0]"
    for i in range(1, len(segments)):
        trans = TRANSITIONS[i % len(TRANSITIONS)]
        # 각 변환 위치의 오프셋: i번째 세그먼트 이전 재생시간 합 - i * td
        offset = round(sum(segment_durations[:i]) - i * td, 2)
        out_label = f"[v{i}]" if i < len(segments) - 1 else "[vout]"
        fc_parts.append(f"{prev}[{i}]xfade=transition={trans}:duration={td}:offset={offset}{out_label}")
        prev = out_label

    fc = ";".join(fc_parts)

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", fc,
        "-map", "[vout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", out_path]
    subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=True)


def _create_tts_audio(text: str, out_path: str, voice: str = "ko-KR-SunHiNeural"):
    """edge-tts를 사용하여 텍스트를 음성으로 변환합니다."""
    clean_text = text.replace("\n", " ").strip()
    if not clean_text:
        return
    print(f"  Generating TTS voiceover: {voice}...")
    cmd = ["edge-tts", "--voice", voice, "--rate=+10%", "--text", clean_text, "--write-media", out_path]
    subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True)


def _get_random_bgm(bgm_dir: Optional[str] = None) -> Optional[str]:
    """static/bgm 폴더에서 랜덤한 mp3 파일을 선택합니다."""
    if bgm_dir is None:
        bgm_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "static", "bgm"))
        
    if not os.path.exists(bgm_dir):
        return None
    bgms = glob.glob(os.path.join(bgm_dir, "*.mp3"))
    if not bgms:
        return None
    return random.choice(bgms)


def _composite_final(
    video_path: str, 
    overlay_path: Optional[str], 
    out_path: str, 
    total_dur: float,
    tts_path: Optional[str] = None,
    bgm_path: Optional[str] = None
):
    """
    영상 위에 시네마틱 필터를 입히고 텍스트 오버레이(시퀀스 또는 단일)를 합성합니다.
    - eq/unsharp: 색감 보정 및 선명도 향상
    - vignette: 하단 가독성을 위한 비네팅 효과
    """
    # 필터 체인: 색감 보정(eq) + 선명도(unsharp) + 비네팅(vignette)
    video_filter = (
        "eq=brightness=0.02:contrast=1.1:saturation=1.2,"
        "unsharp=5:5:1.0:5:5:0.0,"
        "vignette=PI/4"
    )
    
    if overlay_path:
        # 타이핑 자막 이미지 시퀀스 프레임이 존재하는 경우
        # -pix_fmt 없이 overlay=format=auto를 사용해 RGBA 알파 채널을 유지함
        inputs = ["-i", video_path, "-framerate", "30", "-i", overlay_path]
        video_fc = (
            f"[0:v]{video_filter}[v_f];"
            f"[1:v]format=yuva420p[ovr];"
            f"[v_f][ovr]overlay=0:0:format=auto,"
            f"fade=t=in:st=0:d=0.5,fade=t=out:st={total_dur - 0.8}:d=0.8[vout]"
        )
    else:
        # 자막이 없는 경우
        inputs = ["-i", video_path]
        video_fc = (
            f"[0:v]{video_filter},"
            f"fade=t=in:st=0:d=0.5,fade=t=out:st={total_dur - 0.8}:d=0.8[vout]"
        )

    audio_fc = ""
    audio_inputs_start_idx = 2 if overlay_path else 1
    
    # 오디오 믹싱 로직
    fade_st = max(0.0, total_dur - 1.5)
    if tts_path and bgm_path:
        inputs.extend(["-i", tts_path, "-i", bgm_path])
        idx_tts = audio_inputs_start_idx
        idx_bgm = audio_inputs_start_idx + 1
        audio_fc = f"[{idx_tts}:a]volume=1.2[a1];[{idx_bgm}:a]volume=0.25[a2];[a1][a2]amix=inputs=2:duration=longest,afade=t=out:st={fade_st}:d=1.5[aout]"
    elif tts_path:
        inputs.extend(["-i", tts_path])
        idx_tts = audio_inputs_start_idx
        audio_fc = f"[{idx_tts}:a]volume=1.2,afade=t=out:st={fade_st}:d=1.5[aout]"
    elif bgm_path:
        inputs.extend(["-i", bgm_path])
        idx_bgm = audio_inputs_start_idx
        audio_fc = f"[{idx_bgm}:a]volume=0.3,afade=t=out:st={fade_st}:d=1.5[aout]"
    
    fc = video_fc
    if audio_fc:
        fc += ";" + audio_fc

    cmd = ["ffmpeg", "-y"] + inputs + [
           "-filter_complex", fc,
           "-map", "[vout]"]
    
    if audio_fc:
        cmd.extend(["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"])
    
    cmd.extend([
           "-c:v", "libx264", "-preset", "fast", "-crf", "20",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-t", str(total_dur),
           out_path])
    
    print(f"Executing FFmpeg composition: {' '.join(cmd)}")
    subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=True)


# ===== Public API =====

def create_shortform_video(
    image_paths: List[str],
    marketing_text: str,
    output_dir: str = "static/videos",
    seconds_per_image: float = 3.0,
    transition_duration: float = 0.7,
) -> str:
    """
    1~5장의 이미지와 마케팅 텍스트로 프로페셔널 숏폼 영상을 생성합니다.
    
    - BGM 리듬 분석(Librosa)을 반영한 다이내믹 컷 편집
    - 각 이미지에 다양한 Ken Burns 효과 (줌인/줌아웃/패닝)
    - 화면 전환: hblur, zoomin 등 트렌디한 xfade 트랜지션 적용
    - PIL로 동적 타이핑되는 자막 오버레이 프레임 생성
    - 최종 출력: 1080x1920 (9:16) MP4
    """
    os.makedirs(output_dir, exist_ok=True)
    temp = os.path.join(output_dir, f"_tmp_{int(time.time())}")
    os.makedirs(temp, exist_ok=True)

    td = transition_duration
    n = len(image_paths)

    try:
        # 1. 이미지 전처리 (9:16 크롭)
        processed = []
        for i, p in enumerate(image_paths):
            pp = os.path.join(temp, f"img_{i}.png")
            _preprocess_image(p, pp)
            processed.append(pp)

        # 2. BGM 선택 및 비트 분석
        bgm_mp3 = _get_random_bgm()
        
        # 비트 컷 전환 타임스탬프 계산 (N-1개)
        # min_interval은 트랜지션 겹침을 방지하기 위해 td + 1.0초 이상으로 보장
        beat_transitions = _analyze_bgm_beats(
            bgm_mp3, 
            num_segments=n, 
            min_interval=max(1.8, td + 1.0), 
            fallback_interval=seconds_per_image
        )
        
        # 세그먼트별 재생 시간 계산
        segment_durations = []
        if n == 1:
            segment_durations = [seconds_per_image]
            total_dur = seconds_per_image
            td = 0.0  # 단일 이미지는 전환 없음
        else:
            # 첫 번째 세그먼트
            segment_durations.append(beat_transitions[0] + td)
            # 중간 세그먼트들
            for i in range(1, n - 1):
                segment_durations.append(beat_transitions[i] - beat_transitions[i-1] + td)
            # 마지막 세그먼트
            segment_durations.append(seconds_per_image + td)
            
            # 총 재생 시간 (소수점 둘째 자리 반올림)
            total_dur = round(beat_transitions[-1] + seconds_per_image, 2)
            
        print(f"Segment durations: {segment_durations}")
        print(f"Total video duration: {total_dur}s")

        # 3. Ken Burns 세그먼트 생성
        segments = []
        for i, p in enumerate(processed):
            sp = os.path.join(temp, f"seg_{i}.mp4")
            _create_segment(p, sp, i, segment_durations[i])
            segments.append(sp)
            print(f"  Segment {i+1}/{n} created with duration {segment_durations[i]}s.")

        # 4. 트랜지션으로 이어붙이기
        joined = os.path.join(temp, "joined.mp4")
        _join_segments(segments, joined, td, segment_durations)
        print("  Segments joined with transitions.")

        # 5. 자막 애니메이션 오버레이 생성
        overlay_pattern = None
        if marketing_text.strip():
            fps = 30
            total_frames = int(total_dur * fps)
            overlay_pattern = _create_typing_overlay_sequence(
                marketing_text, 
                temp, 
                total_frames, 
                fps
            )
            print("  Subtitle typing sequence generated.")
        
        # 6. TTS 생성
        tts_mp3 = os.path.join(temp, "tts_voice.mp3")
        if marketing_text.strip():
            try:
                _create_tts_audio(marketing_text, tts_mp3)
            except Exception as e:
                print(f"TTS audio generation failed: {e}. Skipping TTS.")
        
        # 7. 최종 합성
        filename = f"shortform_{int(time.time())}.mp4"
        final_path = os.path.join(output_dir, filename)
        
        _composite_final(
            joined, 
            overlay_pattern, 
            final_path, 
            total_dur, 
            tts_path=tts_mp3 if os.path.exists(tts_mp3) else None,
            bgm_path=bgm_mp3
        )

        print(f"Shortform video saved: {final_path} ({total_dur}s)")
        return filename

    except Exception as e:
        print(f"Error in create_shortform_video: {e}")
        raise e
    finally:
        shutil.rmtree(temp, ignore_errors=True)


# ===== Legacy (단일 이미지 → 영상) =====

def generate_video_from_local(
    image_path: str, marketing_text: str = "",
    output_dir: str = "static/videos", duration: int = 10,
) -> str:
    """단일 로컬 이미지를 숏폼 영상으로 변환합니다."""
    return create_shortform_video(
        [image_path], marketing_text, output_dir,
        seconds_per_image=float(duration), transition_duration=0.0
    )
