# Đóng gói ReviewTrans Studio

Kết quả build nằm trong `release/`:

| File | Dùng khi |
|---|---|
| `ReviewTrans-<phiên bản>-setup.exe` | Cài đặt bình thường (không cần quyền admin, cài vào `%LOCALAPPDATA%\Programs\ReviewTrans`, có shortcut và gỡ cài đặt) |
| `ReviewTrans-<phiên bản>-portable.zip` | Giải nén là chạy; mọi dữ liệu (cài đặt, project, model) lưu trong `data\` cạnh `ReviewTrans.exe` |

Cả hai được tạo từ cùng một bản PyInstaller dạng thư mục (`dist\ReviewTrans\`), không dùng dạng 1 file exe
(khởi động chậm vì phải giải nén vài trăm MB mỗi lần, dễ bị antivirus báo nhầm).

## Build trên máy

```bat
build.cmd
build.cmd --version 2.1.0
build.cmd --no-libmpv --skip-installer
```

Cần Python 3.10+ trong PATH. Muốn có installer thì cài [Inno Setup 6](https://jrsoftware.org/isdl.php);
thiếu Inno Setup thì script vẫn tạo zip portable và bỏ qua installer.

`build.cmd` tự tạo venv `.venv_build`, cài thư viện rồi gọi `scripts\build.py`, lần lượt:

1. **tools**: gom ffmpeg, ffprobe, whisper.cpp và libmpv vào `build\tools\`. Ưu tiên lấy từ `bin\` của repo
   hoặc thư mục công cụ của app (`%USERPROFILE%\.video_translation_studio\bin`), không có mới tải về.
2. **app**: PyInstaller theo `build.spec` → `dist\ReviewTrans\`.
3. **check**: chạy `ReviewTrans.exe --self-check` trong môi trường sạch (PATH tối thiểu, thư mục người dùng tạm)
   để chắc bản đóng gói đủ thư viện và công cụ.
4. **zip**: nén bản portable (thêm `portable.txt`).
5. **installer**: Inno Setup theo `installer\ReviewTrans.iss`.

Có thể chạy riêng từng bước: `python scripts\build.py tools|app|check|zip|installer`.

## Build và phát hành tự động trên GitHub

Workflow `.github/workflows/build.yml` (Windows runner):

| Sự kiện | Việc được làm |
|---|---|
| Mở / cập nhật pull request | Chỉ chạy test |
| **Merge (push) vào `main`** | Test → đóng gói → **tự tạo release** |
| Push tag `vX.Y.Z` | Đóng gói → tạo/cập nhật release cho tag đó |
| Bấm **Run workflow** (tab Actions) | Đóng gói, file nằm ở mục **Artifacts** của lần chạy (giữ 14 ngày) |

Quy tắc tự tạo release khi merge vào `main`:

- `APP_VERSION` trong `reviewtrans/__init__.py` **chưa có release** → tạo release chính thức `vX.Y.Z`,
  đánh dấu **Latest**, kèm ghi chú thay đổi tự sinh từ các PR.
- **Đã có** release của phiên bản đó → cập nhật release thử nghiệm **`nightly`** (luôn là bản build mới nhất của
  `main`, thay file mỗi lần merge, không tạo thêm release rác).

**Ra bản chính thức mới:** tăng `APP_VERSION` (ví dụ `2.0.0` → `2.1.0`) trong một PR rồi merge.

Tải bản mới nhất: https://github.com/dominhhieu1405/ReviewTrans/releases/latest

> File cài đặt nằm ở mục **Releases** (cột phải trang repo), không phải **Packages**: Packages của GitHub dành cho
> gói npm/Docker/Maven… nên không dùng cho file `.exe`/`.zip`.

## Chế độ portable

App chạy ở chế độ portable khi có file `portable.txt` cạnh `ReviewTrans.exe` (bản zip có sẵn file này).
Xoá file đó để app dùng thư mục người dùng như bản cài đặt.
