APP_NAME = "ReviewTrans Studio"

try:  # scripts/build.py ghi số phiên bản vào đây khi đóng gói
    from ._build_info import VERSION as APP_VERSION
except ImportError:
    APP_VERSION = "2.0.0"
