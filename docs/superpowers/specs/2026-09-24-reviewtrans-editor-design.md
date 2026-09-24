# ReviewTrans 2 — Editor-style rewrite

## Mục tiêu

Viết lại ReviewTrans thành một ứng dụng dạng phần mềm dựng video:

- Giao diện editor: danh sách câu thoại, khung phát (libmpv, fallback QtMultimedia),
  inspector, timeline nhiều track (layer, phụ đề, lồng tiếng, âm gốc, nhạc nền, video).
- Các trang riêng: Projects, Editor, Hàng đợi, Providers, Presets, Tài nguyên, Công cụ, Cài đặt.
- Project chứa nhiều video, có ngữ cảnh chung (glossary, nhân vật + xưng hô, tóm tắt)
  tự cập nhật bằng LLM sau mỗi video.
- Dịch: Google Translate, Microsoft Translator, API kiểu OpenAI, API kiểu Gemini, Anthropic.
- TTS: Edge TTS, vBee, API kiểu OpenAI `/v1/audio/speech`, Custom API (cũ).
- Bật/tắt lồng tiếng, phụ đề, từng layer; style phụ đề đầy đủ (màu, viền, bóng, hộp nền).
- Layer ảnh, layer chữ, layer vùng che (blur / pixelate / tô màu).

## Nguyên tắc

Một mô hình dữ liệu duy nhất cho mỗi video (`video.json` + `segments.json`).
Player vẽ overlay trực tiếp từ mô hình; `RenderPlanner` dịch mô hình thành
`ffmpeg -filter_complex`. Phụ đề xuất qua ASS (libass); layer chữ được raster hoá
bằng Qt ra PNG nên preview và bản xuất dùng chung một đoạn code vẽ.

## Cấu trúc

```
main.py                     entry point
reviewtrans/
  core/                     không phụ thuộc widget Qt (chỉ QtGui cho raster chữ)
    paths.py proc.py srt.py langs.py models.py config.py store.py
    context.py ass.py
    providers/translate/*   google, microsoft, openai_compat, gemini_compat, anthropic
    providers/tts/*         edge, vbee, openai_speech, legacy_custom
    pipeline/*              asr, translate, tts, mix, render, runner, resources
  ui/
    app.py main_window.py theme.py icons.py state.py jobs.py
    player/                 mpv (render API trong QOpenGLWidget) + Qt fallback
    widgets/                timeline, segment table, style editor, layer inspector...
    pages/                  projects, editor, queue, providers, presets, resources, tools, settings
legacy/                     mã nguồn cũ (tham khảo, không bảo trì)
```

## Lưu trữ

```
<Project>/project.json      thông tin project, mặc định, danh sách video theo thứ tự tập
<Project>/context.json      ngữ cảnh chung
<Project>/videos/<id>/video.json     timeline: style, layers, audio, track on/off, trạng thái
<Project>/videos/<id>/segments.json  câu gốc, bản dịch, trạng thái TTS
<Project>/videos/<id>/cache/         audio.wav, asr.srt, tts/, mix.wav, layer PNG
<Project>/output/                    video xuất, srt
~/.video_translation_studio/settings.json  providers, presets, cài đặt (tự migrate config.json cũ)
```

## Pipeline

`asr → translate → context → tts → mix → render`, mỗi bước cache được và chạy lại riêng.
Hàng đợi chạy tuần tự theo thứ tự tập để ngữ cảnh luôn mới nhất.
Sửa một câu chỉ làm câu đó "stale" và chỉ TTS lại câu đó.
