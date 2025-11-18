import os
import sys

import serial
import serial.tools.list_ports

from PyQt5 import uic
from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QSlider, QMessageBox

import pyqtgraph as pg


def resource_path(relative_path: str) -> str:
    """
    Trả về đường dẫn thực tế của file resource (VD: dashboard_1.ui),
    dùng được cả khi chạy .py bình thường và khi đóng gói PyInstaller.
    """
    if hasattr(sys, "_MEIPASS"):
        # Khi chạy từ file .exe do PyInstaller tạo
        base_path = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        # Khi chạy bằng python main.py
        base_path = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base_path, relative_path)


class PSWKitWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        # Load giao diện từ file .ui
        uic.loadUi(resource_path("dashboard_1.ui"), self)

        # Cố định kích thước cửa sổ
        self.setFixedSize(1010, 885)

        # Đặt icon cho cửa sổ (title bar + taskbar)
        self.setWindowIcon(QIcon(resource_path("psw.ico")))

        # ===== Biến trạng thái =====
        self.ser = None
        self.led_on = False          # LED on-board (SPARE2) – hiện đang không dùng nút
        self.relay_state = {1: False, 2: False, 3: False, 4: False}
        self.handshake_ok = False    # đã nhận KIT=... sau INFO hay chưa
        self.sio_state = {1: False, 2: False, 3: False, 4: False}

        # ===== Gắn signal cho các nút chính =====
        self.btnRefresh.clicked.connect(self.refresh_ports)
        self.btnConnect.clicked.connect(self.toggle_connect)

        self.btnR1.clicked.connect(lambda: self.toggle_relay(1, self.btnR1))
        self.btnR2.clicked.connect(lambda: self.toggle_relay(2, self.btnR2))
        self.btnR3.clicked.connect(lambda: self.toggle_relay(3, self.btnR3))
        self.btnR4.clicked.connect(lambda: self.toggle_relay(4, self.btnR4))

        self.btnBuz.clicked.connect(lambda: self.send_cmd("BUZ"))
        # self.btnLed.clicked.connect(self.toggle_led)  # nếu cần thì mở lại
        self.btnRead.clicked.connect(lambda: self.send_cmd("READ"))

        self.btnClean.clicked.connect(lambda: self.logg.clear())

        # Auto READ
        self.checkAutoRead.stateChanged.connect(self.on_auto_read_changed)

        # ===== Điều khiển OLED (2 dòng) =====
        self.btnOled1.clicked.connect(self.send_oled1)
        self.btnOled2.clicked.connect(self.send_oled2)

        # ===== Điều khiển ADS1115 (A0, A1) =====
        self.btnAdsLoad.clicked.connect(self.load_ads)

        # ===== Ô nhập lệnh trực tiếp =====
        self.btnCmdSend.clicked.connect(self.send_custom_cmd)
        self.editCmd.returnPressed.connect(self.send_custom_cmd)

        # ===== Help / API =====
        self.btnHelp.clicked.connect(self.show_help)

        # ===== I/O SPARE (SIO1..SIO4) =====
        self.checkSIO1.stateChanged.connect(lambda s: self.set_sio(1, s))
        self.checkSIO2.stateChanged.connect(lambda s: self.set_sio(2, s))
        self.checkSIO3.stateChanged.connect(lambda s: self.set_sio(3, s))
        self.checkSIO4.stateChanged.connect(lambda s: self.set_sio(4, s))

        # ===== Timer đọc Serial =====
        self.timer = QTimer()
        self.timer.setInterval(100)                # 100 ms
        self.timer.timeout.connect(self.read_serial)

        # ===== Timer Auto READ (gửi READ định kỳ) =====
        self.auto_timer = QTimer()
        self.auto_timer.setInterval(500)           # 500 ms
        self.auto_timer.timeout.connect(self.auto_read_tick)

        # ===== Slider RGB cho WS2812 =====
        self.sliderR = self.findChild(QSlider, "sliderR")
        self.sliderG = self.findChild(QSlider, "sliderG")
        self.sliderB = self.findChild(QSlider, "sliderB")

        for s in (self.sliderR, self.sliderG, self.sliderB):
            if isinstance(s, QSlider):
                s.setMinimum(0)
                s.setMaximum(255)
            else:
                if s is not None:
                    self.log(f"WARNING: {s.objectName()} khong phai QSlider (type={type(s)})")
                else:
                    self.log("WARNING: Khong tim thay sliderR / sliderG / sliderB trong .ui")

        if isinstance(self.sliderR, QSlider):
            self.sliderR.valueChanged.connect(self.update_rgb_labels)
            self.sliderR.sliderReleased.connect(self.send_rgb_from_sliders)
        if isinstance(self.sliderG, QSlider):
            self.sliderG.valueChanged.connect(self.update_rgb_labels)
            self.sliderG.sliderReleased.connect(self.send_rgb_from_sliders)
        if isinstance(self.sliderB, QSlider):
            self.sliderB.valueChanged.connect(self.update_rgb_labels)
            self.sliderB.sliderReleased.connect(self.send_rgb_from_sliders)

        # ===== Khởi tạo ban đầu =====
        self.refresh_ports()
        self.reset_status_labels()
        self.update_rgb_labels()
        self.update_conn_label(False)
        self.update_all_relay_labels()

        # ===== Khởi tạo đồ thị ADC1 =====
        self.adc_history = []
        self.history_len = 200
        self.init_plot()

    # ------------------------------------------------------------------
    # Khởi tạo pyqtgraph vào plotWidget
    # ------------------------------------------------------------------
    def init_plot(self):
        layout = QVBoxLayout(self.plotWidget)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot = pg.PlotWidget()
        layout.addWidget(self.plot)

        self.plot.setTitle("ADC1 realtime")
        self.plot.setLabel("left", "Value")
        self.plot.setLabel("bottom", "Sample")
        self.plot.showGrid(x=True, y=True)

        self.plot_curve = self.plot.plot([], [], pen='y')

    # ------------------------------------------------------------------
    # Serial: quét cổng
    # ------------------------------------------------------------------
    def refresh_ports(self):
        self.comboPort.clear()
        ports = serial.tools.list_ports.comports()
        for p in ports:
            self.comboPort.addItem(p.device)
        self.log("Ports refreshed.")

    # ------------------------------------------------------------------
    # Cập nhật label trạng thái kết nối
    # ------------------------------------------------------------------
    def update_conn_label(self, connected: bool):
        if connected:
            text = "CONNECTED"
            style = (
                "background-color: rgb(0, 200, 0);"
                "color: white;"
                "border: 1px solid black;"
                "padding: 2px;"
            )
        else:
            text = "DISCONNECTED"
            style = (
                "background-color: rgb(150, 75, 0);"
                "color: white;"
                "border: 1px solid black;"
                "padding: 2px;"
            )

        self.labelStatus.setText(text)
        self.labelStatus.setStyleSheet(style)

    # ------------------------------------------------------------------
    # Cập nhật label trạng thái relay
    # ------------------------------------------------------------------
    def update_relay_label(self, idx: int, state: bool):
        label_map = {
            1: self.labelR1State,
            2: self.labelR2State,
            3: self.labelR3State,
            4: self.labelR4State,
        }
        lbl = label_map.get(idx)
        if lbl is None:
            return

        if state:
            text = "ON"
            style = (
                "background-color: rgb(0, 200, 0);"
                "color: white;"
                "border: 1px solid black;"
                "padding: 2px;"
            )
        else:
            text = "OFF"
            style = (
                "background-color: rgb(150, 75, 0);"
                "color: white;"
                "border: 1px solid black;"
                "padding: 2px;"
            )
        lbl.setText(text)
        lbl.setStyleSheet(style)

    def update_all_relay_labels(self):
        for i in range(1, 5):
            state = self.relay_state.get(i, False)
            self.update_relay_label(i, state)

    # ------------------------------------------------------------------
    # Connect / Disconnect
    # ------------------------------------------------------------------
    def toggle_connect(self, checked):
        if checked:
            port = self.comboPort.currentText()
            if not port:
                self.log("No COM port selected.")
                self.btnConnect.setChecked(False)
                self.update_conn_label(False)
                return

            try:
                self.ser = serial.Serial(port, 115200, timeout=0.1)
                self.log(f"Connected to {port}")
                self.btnConnect.setText("Disconnect")
                self.timer.start()
                self.update_conn_label(True)

                self.handshake_ok = False
                self.send_cmd("INFO")

            except Exception as e:
                self.log(f"Connect failed: {e}")
                self.ser = None
                self.btnConnect.setChecked(False)
                self.update_conn_label(False)
        else:
            self.timer.stop()
            self.auto_timer.stop()
            self.checkAutoRead.setChecked(False)
            if self.ser:
                self.ser.close()
                self.ser = None
            self.btnConnect.setText("Connect")
            self.log("Disconnected.")
            self.update_conn_label(False)
            self.handshake_ok = False

            # Reset SIO khi disconnect cho đồng bộ UI
            self.checkSIO1.setChecked(False)
            self.checkSIO2.setChecked(False)
            self.checkSIO3.setChecked(False)
            self.checkSIO4.setChecked(False)
            self.sio_state = {1: False, 2: False, 3: False, 4: False}

    # ------------------------------------------------------------------
    # Gửi lệnh xuống ESP32
    # ------------------------------------------------------------------
    def send_cmd(self, cmd: str):
        if not self.ser or not self.ser.is_open:
            self.log("Not connected.")
            return
        try:
            line = (cmd + "\n").encode("utf-8")
            self.ser.write(line)
            self.log(f">>> {cmd}")
        except Exception as e:
            self.log(f"Send error: {e}")

    # ------------------------------------------------------------------
    # Đọc dữ liệu từ Serial
    # ------------------------------------------------------------------
    def read_serial(self):
        if not self.ser or not self.ser.is_open:
            return
        try:
            while self.ser.in_waiting:
                line = self.ser.readline().decode("utf-8", errors="ignore").strip()
                if line:
                    self.log(f"<<< {line}")
                    self.parse_line(line)
        except Exception as e:
            self.log(f"Read error: {e}")

    # ------------------------------------------------------------------
    # Auto READ: checkbox & timer
    # ------------------------------------------------------------------
    def on_auto_read_changed(self, state):
        if state != 0:
            if self.ser and self.ser.is_open:
                self.auto_timer.start()
                self.log("Auto READ: ON")
            else:
                self.log("Cannot enable Auto READ: not connected.")
                self.checkAutoRead.setChecked(False)
        else:
            self.auto_timer.stop()
            self.log("Auto READ: OFF")

    def auto_read_tick(self):
        if self.ser and self.ser.is_open:
            self.send_cmd("READ")
        else:
            self.auto_timer.stop()
            self.checkAutoRead.setChecked(False)

    # ------------------------------------------------------------------
    # Phân tích dữ liệu từ ESP32
    # ------------------------------------------------------------------
    def parse_line(self, line: str):

        # Thông tin board trả về sau INFO: KIT=ESP32;FW=...
        if line.startswith("KIT="):
            if not self.handshake_ok:
                self.handshake_ok = True
                self.send_cmd("BUZ")   # gọi buzzer trên board
            return

        # STATUS;ADC=...;S=...;
        if line.startswith("STATUS;"):
            try:
                parts = line.split(";")
                adc_vals = None
                s_vals = None

                for p in parts:
                    if p.startswith("ADC="):
                        adc_str = p[4:]
                        adc_vals = [int(x) for x in adc_str.split(",") if x != ""]
                    elif p.startswith("S="):
                        s_str = p[2:]
                        s_vals = [int(x) for x in s_str.split(",") if x != ""]

                # Cập nhật ADC (4 kênh)
                if adc_vals:
                    if len(adc_vals) > 0:
                        self.labelADC1.setText(str(adc_vals[0]))
                    if len(adc_vals) > 1:
                        self.labelADC2.setText(str(adc_vals[1]))
                    if len(adc_vals) > 2:
                        self.labelADC3.setText(str(adc_vals[2]))
                    if len(adc_vals) > 3:
                        self.labelADC4.setText(str(adc_vals[3]))
                    self.update_adc_plot(adc_vals[0])

                # Cập nhật Sensor (8 kênh)
                if s_vals:
                    if len(s_vals) > 0:
                        self.labelS1.setText(str(s_vals[0]))
                    if len(s_vals) > 1:
                        self.labelS2.setText(str(s_vals[1]))
                    if len(s_vals) > 2:
                        self.labelS3.setText(str(s_vals[2]))
                    if len(s_vals) > 3:
                        self.labelS4.setText(str(s_vals[3]))
                    if len(s_vals) > 4:
                        self.labelS5.setText(str(s_vals[4]))
                    if len(s_vals) > 5:
                        self.labelS6.setText(str(s_vals[5]))
                    if len(s_vals) > 6:
                        self.labelS7.setText(str(s_vals[6]))
                    if len(s_vals) > 7:
                        self.labelS8.setText(str(s_vals[7]))   

            except Exception as e:
                self.log(f"Parse STATUS error: {e}")

        # ADS;A0=xxxx;A1=yyyy;
        elif line.startswith("ADS;"):
            try:
                parts = line.split(";")
                a0_val = None
                a1_val = None
                for p in parts:
                    if p.startswith("A0="):
                        a0_val = int(p[3:])
                    elif p.startswith("A1="):
                        a1_val = int(p[3:])

                if a0_val is not None:
                    self.labelADS0.setText(str(a0_val))
                if a1_val is not None:
                    self.labelADS1.setText(str(a1_val))
            except Exception as e:
                self.log(f"Parse ADS error: {e}")

    # ------------------------------------------------------------------
    # Cập nhật đồ thị ADC1
    # ------------------------------------------------------------------
    def update_adc_plot(self, adc1_value: int):
        self.adc_history.append(adc1_value)
        if len(self.adc_history) > self.history_len:
            self.adc_history = self.adc_history[-self.history_len:]

        x = list(range(len(self.adc_history)))
        y = self.adc_history
        self.plot_curve.setData(x, y)

    # ------------------------------------------------------------------
    # Điều khiển Relay & LED on-board
    # ------------------------------------------------------------------
    def toggle_relay(self, idx: int, btn):
        self.relay_state[idx] = not self.relay_state[idx]
        state = "ON" if self.relay_state[idx] else "OFF"
        self.send_cmd(f"R{idx} {state}")
        btn.setText(f"R{idx} {state}")
        self.update_relay_label(idx, self.relay_state[idx])

    def toggle_led(self):
        self.led_on = not self.led_on
        if self.led_on:
            self.send_cmd("LED ON")
            self.btnLed.setText("LED OFF")
        else:
            self.send_cmd("LED OFF")
            self.btnLed.setText("LED ON")

    # ------------------------------------------------------------------
    # RGB Slider cho WS2812
    # ------------------------------------------------------------------
    def update_rgb_labels(self):
        r = self.sliderR.value() if isinstance(self.sliderR, QSlider) else 0
        g = self.sliderG.value() if isinstance(self.sliderG, QSlider) else 0
        b = self.sliderB.value() if isinstance(self.sliderB, QSlider) else 0

        self.labelRVal.setText(str(r))
        self.labelGVal.setText(str(g))
        self.labelBVal.setText(str(b))

        self.update_color_preview(r, g, b)

    def update_color_preview(self, r, g, b):
        brightness = 0.299 * r + 0.587 * g + 0.114 * b
        text_color = "white" if brightness < 128 else "black"

        self.labelColorPreview.setText(f"{r},{g},{b}")
        self.labelColorPreview.setStyleSheet(
            f"background-color: rgb({r}, {g}, {b});"
            f"color: {text_color};"
            "border: 1px solid black;"
        )

    def send_rgb_from_sliders(self):
        if not (isinstance(self.sliderR, QSlider) and
                isinstance(self.sliderG, QSlider) and
                isinstance(self.sliderB, QSlider)):
            self.log("RGB sliders not ready (check .ui).")
            return

        r = self.sliderR.value()
        g = self.sliderG.value()
        b = self.sliderB.value()
        cmd = f"RGB {r},{g},{b}"
        self.send_cmd(cmd)

    # ------------------------------------------------------------------
    # Gửi dữ liệu OLED
    # ------------------------------------------------------------------
    def send_oled1(self):
        txt = self.editOled1.text()
        self.send_cmd(f"OL1 {txt}")

    def send_oled2(self):
        txt = self.editOled2.text()
        self.send_cmd(f"OL2 {txt}")

    # ------------------------------------------------------------------
    # Gửi lệnh đọc ADS1115 A0/A1
    # ------------------------------------------------------------------
    def load_ads(self):
        self.send_cmd("ADS")

    # ------------------------------------------------------------------
    # Console mini: gửi lệnh tùy ý
    # ------------------------------------------------------------------
    def send_custom_cmd(self):
        cmd = self.editCmd.text().strip()
        if not cmd:
            self.log("Empty command, not sent.")
            return

        self.send_cmd(cmd)
        self.editCmd.selectAll()

    # ------------------------------------------------------------------
    # Điều khiển SIO từ checkbox
    # ------------------------------------------------------------------
    def set_sio(self, idx: int, state: int):
        """
        state: 0 = unchecked (OFF), 2 = checked (ON)
        """
        on = (state != 0)
        self.sio_state[idx] = on
        cmd = f"SIO{idx} {'ON' if on else 'OFF'}"
        self.send_cmd(cmd)

    # ------------------------------------------------------------------
    # Help / API
    # ------------------------------------------------------------------
    def show_help(self):
        text = (
            "ESP32 KIT – Serial API\n\n"
            "Protocol:\n"
            "  - Baud: 115200, 8N1, ASCII\n"
            "  - Mỗi lệnh kết thúc bằng CR/LF (\\r\\n)\n\n"
            "Lệnh cơ bản:\n"
            "  PING            → PONG\n"
            "  INFO            → KIT=ESP32;FW=x.y;\n\n"
            "Đọc trạng thái:\n"
            "  READ            → STATUS;ADC=a1,a2,a3,a4;S=s1,s2,s3,s4,s5,s6,s7,s8;\n"
            "  ADS             → ADS;A0=xxxx;A1=yyyy;\n\n"
            "Relay (4 kênh):\n"
            "  R1 ON / R1 OFF\n"
            "  R2 ON / R2 OFF\n"
            "  R3 ON / R3 OFF\n"
            "  R4 ON / R4 OFF\n\n"
            "LED on-board (SPARE2 – GPIO2):\n"
            "  LED ON\n"
            "  LED OFF\n\n"
            "WS2812 (1 LED RGB):\n"
            "  RGB R,G,B       (R,G,B: 0–255)\n"
            "    VD: RGB 255,0,128\n\n"
            "OLED 128x64 I2C:\n"
            "  OL1 <text>      → ghi dòng 1\n"
            "  OL2 <text>      → ghi dòng 2\n"
            "    VD: OL1 Hung Tran\n"
            "    VD: OL2 -SEHC-\n\n"
            "I/O SPARE (output):\n"
            "   SIO1 ON / SIO1 OFF\n"
            "   SIO2 ON / SIO2 OFF\n"
            "   SIO3 ON / SIO3 OFF\n"
            "   SIO4 ON / SIO4 OFF\n\n"
            "Gợi ý test bằng Docklight / terminal:\n"
            "  - Gửi: PING\\r\\n  → nhận: PONG\n"
            "  - Gửi: READ\\r\\n  → nhận: STATUS;...\n"
            "  - Gửi: ADS\\r\\n   → nhận: ADS;A0=...;A1=...;\n"
        )

        QMessageBox.information(self, "Help – Serial API", text)

    # ------------------------------------------------------------------
    # Reset labels
    # ------------------------------------------------------------------
    def reset_status_labels(self):
        self.labelADC1.setText("-")
        self.labelADC2.setText("-")
        self.labelADC3.setText("-")
        self.labelADC4.setText("-")

        self.labelS1.setText("-")
        self.labelS2.setText("-")
        self.labelS3.setText("-")
        self.labelS4.setText("-")
        self.labelS5.setText("-")
        self.labelS6.setText("-")
        self.labelS7.setText("-")
        self.labelS8.setText("-")

        self.labelADS0.setText("-")
        self.labelADS1.setText("-")

    def log(self, text: str):
        self.logg.append(text)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = PSWKitWindow()
    win.setWindowTitle("ESP32 KIT Tester (Dashboard)")
    win.show()
    sys.exit(app.exec_())
