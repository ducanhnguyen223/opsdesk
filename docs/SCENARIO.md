# OpsDesk: một ca trực điều phối logistics

Đây là kịch bản portfolio độc lập, không phải hệ thống của một doanh nghiệp thật.
Tên khách hàng, hãng vận chuyển, giá trị đơn, thư đến và SLA đều giả lập.
Không có dữ liệu cá nhân, thông tin doanh nghiệp cũ hoặc credential được tái sử dụng.

## Vai trò và công việc

Bạn là nhân viên điều phối vận tải của Alpha Logistics. Đầu ca, mở hàng đợi để
ưu tiên các hồ sơ quá hạn phản hồi, sau đó đến mức độ khẩn cấp và hạn gần nhất.
Beta Logistics là tenant riêng dùng để kiểm chứng cách ly dữ liệu.

`EX-9000` / `SHP-9000`: một lô linh kiện Hải Phòng → Hà Nội dự kiến chậm 48 giờ.
Hai đơn còn hiệu lực bị ảnh hưởng; đơn thứ ba đã hủy và không được đưa vào đề xuất.
Hãng vận chuyển có thông báo nguồn; hệ thống có lịch giao, khách hàng và quy trình
riêng cho phạm vi standard. Một số hồ sơ khác thiếu hoặc mâu thuẫn quy trình để
thử luồng từ chối tự động đưa ra quyết định.

## Luồng thao tác

1. Đăng nhập demo Alpha / Nhân viên vận hành, lọc `SHP-9000`.
2. Mở hồ sơ; đọc thông báo, lịch giao, đơn hàng, SLA và nhật ký.
3. Nhận xử lý. Hệ thống gán người hiện tại và chuyển sang Đang xử lý.
4. Phân tích hồ sơ; kiểm tra dữ kiện, đơn bị ảnh hưởng và trích dẫn quy trình.
5. Tích xác nhận và duyệt: tạo ticket nội bộ, sau đó liên kết vào hồ sơ.
6. Ghi chú và chuyển sang Chờ hãng vận chuyển hoặc Đã chuyển cấp nếu cần.
7. Khi có kết quả, ghi lý do và chuyển Đã giải quyết. Có thể mở lại để điều tra.

### Bản nháp phản hồi

Sau khi phân tích, chọn **Đưa vào bản nháp**, sửa người nhận/bộ phận, tiêu đề và
nội dung rồi **Lưu bản nháp**. Mỗi lần lưu tạo phiên bản mới và một mục nhật ký.
Nội dung sửa tay không tự trở thành quyết định được duyệt. Hệ thống không gửi thư.
Nếu bằng chứng đã thay đổi, phân tích lại rồi gắn nguồn mới trước khi lưu.
Nếu phiên bản bản nháp đã thay đổi bởi một lượt khác, tải lại trước khi sửa tiếp.
Rời hồ sơ khi chưa lưu bị chặn; nút **Bỏ thay đổi chưa lưu** chỉ bỏ phần đang sửa,
không xóa bản đã lưu hoặc lịch sử. Tải lại/đóng tab có cảnh báo trình duyệt khi hỗ trợ.

Không có thư nào tự gửi. “Chờ hãng vận chuyển” là trạng thái do người vận hành
ghi nhận, không phải bằng chứng hệ thống đã liên hệ một hãng thật.

## Dữ liệu và kiểm soát

- Seed 36 hồ sơ, 108 đơn bổ sung, 36 lô hàng, chỉ khi bật demo login.
- Seed một lần; restart không xóa thao tác, đổi deadline hay reset trạng thái.
- API bảo vệ tenant và role; thay đổi dùng expected_revision và transaction.
- Đóng hồ sơ yêu cầu ticket được duyệt, chủ sở hữu và lý do.
- Liên kết ticket kiểm tra tenant, chủ sở hữu analysis và mã lô hàng.
- Tạo ticket và liên kết hồ sơ là hai request; nếu liên kết lỗi, duyệt lại cùng
  analysis dùng cơ chế idempotency hiện có rồi thử liên kết, không tạo ticket trùng.
- Ở quy mô demo, nhật ký lưu trong JSON cùng hồ sơ; chưa phải immutable audit store
  chống sửa trực tiếp cơ sở dữ liệu. Public production cần thiết kế lưu trữ phù hợp.
- Phản hồi AI hiện là deterministic offline; không được trình bày như GPT đang chạy.

Kiểm tra: `.venv/bin/python -m unittest discover -s tests -q`.
