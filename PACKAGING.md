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

## Build tự động trên GitHub

Workflow `.github/workflows/build.yml` (Windows runner):

- Push lên `main`, mở pull request, hoặc bấm **Run workflow** trong tab Actions → chạy test rồi đóng gói;
  file installer + zip nằm ở mục **Artifacts** của lần chạy (giữ 14 ngày).
- Push tag phiên bản → tạo **GitHub Release** kèm installer + zip:

  ```bash
  git tag v2.1.0
  git push origin v2.1.0
  ```

  Tag có dấu gạch (vd. `v2.1.0-beta.1`) được đánh dấu là pre-release.

## Chế độ portable

App chạy ở chế độ portable khi có file `portable.txt` cạnh `ReviewTrans.exe` (bản zip có sẵn file này).
Xoá file đó để app dùng thư mục người dùng như bản cài đặt.
