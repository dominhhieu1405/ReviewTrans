# ReviewTrans Studio

Công cụ dịch, lồng tiếng và chèn phụ đề tự động cho video review phim, với giao diện kiểu phần mềm dựng video.

## Tính năng

- **Project nhiều video**: mỗi project (một bộ phim) chứa nhiều video xếp theo thứ tự tập, dùng chung
  **ngữ cảnh**: tóm tắt cốt truyện, nhân vật + cách xưng hô, thuật ngữ. Sau mỗi video, LLM tự bổ sung ngữ cảnh.
  Mục nào đã “khoá” sẽ không bị ghi đè.
- **Editor**: bảng câu thoại (sửa trực tiếp, tách/gộp/khoá câu), khung phát libmpv (tự chuyển sang Qt Multimedia
  nếu chưa có libmpv), inspector, timeline nhiều track (layer, phụ đề, lồng tiếng, âm gốc, nhạc nền, video).
- **Dịch**: Google Translate, Microsoft Translator (không cần key hoặc dùng key Azure/Google Cloud),
  API kiểu OpenAI (OpenAI, DeepSeek, OpenRouter, LM Studio…), API kiểu Gemini, Anthropic Claude. Hỗ trợ nhiều key xoay vòng.
- **Lồng tiếng**: Edge TTS, vBee, API kiểu OpenAI `/v1/audio/speech`, Custom API bản cũ. Có thể đặt giọng riêng
  cho từng nhân vật hoặc từng câu. Sửa câu nào chỉ tạo lại lồng tiếng câu đó.
- **Bật/tắt** phụ đề, lồng tiếng, âm gốc, nhạc nền, từng layer.
- **Style phụ đề**: font, cỡ, màu chữ, viền, bóng, hộp nền, vị trí, lề, số ký tự mỗi dòng. Lưu thành preset.
- **Layer**: ảnh (logo), chữ, vùng che (làm mờ / pixel hoá / tô màu) để che chữ cứng của video gốc.
  Kéo thả trên khung phát và trên timeline.
- **Hàng đợi**: chạy hàng loạt Nhận dạng → Dịch → Cập nhật ngữ cảnh → Lồng tiếng → Trộn âm → Xuất video.
- Trang riêng cho **Providers**, **Preset**, **Tài nguyên** (tải ffmpeg, whisper.cpp, model Whisper, libmpv),
  **Công cụ** (ghép video), **Cài đặt**.

## Chạy từ mã nguồn

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

Lần đầu mở, vào trang **Tài nguyên** để tải FFmpeg, whisper.cpp và libmpv (player). Cấu hình cũ
(`~/.video_translation_studio/config.json`) được tự chuyển thành các provider.

## Cấu trúc

```
main.py                   điểm khởi chạy
reviewtrans/core/         mô hình dữ liệu, provider, pipeline (không phụ thuộc widget)
reviewtrans/ui/           giao diện PyQt6 (player, timeline, các trang)
tests/                    pytest (có test render thật bằng ffmpeg)
legacy/                   mã nguồn bản cũ, chỉ để tham khảo
```

Dữ liệu một project nằm trong thư mục của nó (`project.json`, `context.json`, `videos/<id>/…`, `output/`),
có thể sao chép hoặc sao lưu nguyên thư mục.

## Test

```bash
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
```
