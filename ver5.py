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
        # Khi chạy .py bình thường
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


class PSWKitWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        # Load giao diện từ file .ui
        uic.loadUi(resource_path("dashboard_2.ui"), self)

        # Cố định kích thước cửa sổ
        self.setFixedSize(1274, 876)

        # Đặt icon cho cửa sổ (title bar + taskbar)
        self.setWindowIcon(QIcon(resource_path("psw.ico")))

        # ===== Biến trạng thái =====
        self.ser = None
        self.led_on = False          # LED on-board (SPARE2) – hiện đang không dùng nút

        # 16 relay output (R1..R16)
        self.relay_state = {i: False for i in range(1, 17)}

        # Đã nhận KIT=... sau INFO hay chưa
        self.handshake_ok = False

        # 6 ngõ I/O SPARE (SIO1..SIO6)
        self.sio_state = {i: False for i in range(1, 7)}

        # ===== Gắn signal cho các nút chính =====
        self.btnRefresh.clicked.connect(self.refresh_ports)
        self.btnConnect.clicked.connect(self.toggle_connect)

        # Relay buttons O1..O16
        self.relay_buttons = {}
        for i in range(1, 17):
            btn = getattr(self, f"btnR{i}", None)
            if btn is not None:
                self.relay_buttons[i] = btn
                btn.clicked.connect(
                    lambda _checked, idx=i, b=btn: self.toggle_relay(idx, b)
                )

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

        # ===== I/O SPARE (SIO1..SIO6) =====
        for i in range(1, 7):
            cb = getattr(self, f"checkSIO{i}", None)
            if cb is not None:
                cb.stateChanged.connect(lambda state, idx=i: self.set_sio(idx, state))

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

        # ===== Plot cho ADC1 (Realtime) =====
        self.plotWidget: pg.GraphicsLayoutWidget
        layout = QVBoxLayout(self.plotWidget)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot = pg.PlotWidget()
        layout.addWidget(self.plot)

        self.plot.setLabel("left", "ADC1 Value")
        self.plot.setLabel("bottom", "Samples")
        self.plot.showGrid(x=True, y=True)
        self.plot_data = []
        self.max_points = 200

        self.curve = self.plot.plot([], [])

        # ===== Khởi tạo ban đầu =====
        self.refresh_ports()
        self.reset_status_labels()
        self.update_rgb_labels()
        self.update_conn_label(False)
        self.update_all_relay_labels()

    # ------------------------------------------------------------------
    # COM port
    # ------------------------------------------------------------------
    def refresh_ports(self):
        self.comboPort.clear()
        ports = serial.tools.list_ports.comports()
        for p in ports:
            self.comboPort.addItem(p.device)
        self.log("Ports refreshed.")

    def update_conn_label(self, connected: bool):
        if connected:
            self.labelConn.setText("CONNECTED")
            self.labelConn.setStyleSheet(
                "background-color: rgb(0, 200, 0);"
                "color: white;"
                "font-weight: bold;"
            )
        else:
            self.labelConn.setText("DISCONNECTED")
            self.labelConn.setStyleSheet(
                "background-color: rgb(200, 0, 0);"
                "color: white;"
                "font-weight: bold;"
            )

    # ------------------------------------------------------------------
    # Timer Auto READ
    # ------------------------------------------------------------------
    def on_auto_read_changed(self, state: int):
        """
        state = 0 (unchecked), 2 (checked)
        """
        if state == 2:
            # Bật auto-timer
            if not self.auto_timer.isActive():
                self.auto_timer.start()
                self.log("Auto READ ON")
        else:
            # Tắt auto-timer
            if self.auto_timer.isActive():
                self.auto_timer.stop()
                self.log("Auto READ OFF")

    def auto_read_tick(self):
        """
        Hàm này được gọi định kỳ bởi self.auto_timer.
        """
        if self.ser and self.ser.is_open:
            self.send_cmd("READ")

    # ------------------------------------------------------------------
    # Plot ADC (Realtime)
    # ------------------------------------------------------------------
    def update_adc_plot(self, new_value: int):
        self.plot_data.append(new_value)
        if len(self.plot_data) > self.max_points:
            self.plot_data = self.plot_data[-self.max_points:]

        x = list(range(len(self.plot_data)))
        self.curve.setData(x, self.plot_data)

    # ------------------------------------------------------------------
    # Điều khiển Relay
    
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

    def update_relay_label(self, idx: int, state: bool):
        """
        Cập nhật labelR{idx}State theo trạng thái relay (ON/OFF)
        Ví dụ: idx=1 → labelR1State
        """
        lbl = getattr(self, f"labelR{idx}State", None)
        if lbl is None:
            return

        if state:
            text = "ON"
            style = (
                "background-color: rgb(0, 180, 0);"
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
        """
        Gọi lại khi khởi động app để sync tất cả labelR1State..labelR16State
        với self.relay_state (ban đầu đều False = OFF).
        """
        for i, st in self.relay_state.items():
            self.update_relay_label(i, st)

    # RGB Slider cho WS2812
    # ------------------------------------------------------------------
    def update_rgb_labels(self):
        r = self.sliderR.value() if isinstance(self.sliderR, QSlider) else 0
        g = self.sliderG.value() if isinstance(self.sliderG, QSlider) else 0
        b = self.sliderB.value() if isinstance(self.sliderB, QSlider) else 0

        self.labelRVal.setText(str(r))
        self.labelGVal.setText(str(g))
        self.labelBVal.setText(str(b))

    def send_rgb_from_sliders(self):
        r = self.sliderR.value() if isinstance(self.sliderR, QSlider) else 0
        g = self.sliderG.value() if isinstance(self.sliderG, QSlider) else 0
        b = self.sliderB.value() if isinstance(self.sliderB, QSlider) else 0
        cmd = f"RGB {r},{g},{b}"
        self.send_cmd(cmd)

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

                # Sau khi connect, gửi INFO để đọc KIT=...
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
            for i in range(1, 7):
                cb = getattr(self, f"checkSIO{i}", None)
                if cb is not None:
                    cb.setChecked(False)
            self.sio_state = {i: False for i in range(1, 7)}

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
    # Đọc Serial
    # ------------------------------------------------------------------
    def read_serial(self):
        if not self.ser or not self.ser.is_open:
            return

        try:
            while self.ser.in_waiting > 0:
                raw = self.ser.readline()
                if not raw:
                    break

                try:
                    line = raw.decode("utf-8", errors="ignore").strip()
                except UnicodeDecodeError:
                    continue

                if not line:
                    continue

                self.log(f"<<< {line}")
                self.parse_line(line)

        except Exception as e:
            self.log(f"Read error: {e}")

    # ------------------------------------------------------------------
    # Parse dữ liệu trả về
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
                        if adc_str:
                            adc_vals = [int(x) for x in adc_str.split(",") if x != ""]
                    elif p.startswith("S="):
                        s_str = p[2:]
                        if s_str:
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

                # Cập nhật Sensor (tối đa 16 kênh)
                if s_vals:
                    for i, val in enumerate(s_vals, start=1):
                        lbl = getattr(self, f"labelS{i}", None)
                        if lbl is not None:
                            lbl.setText(str(val))

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
    # Gửi text cho OLED
    # ------------------------------------------------------------------
    def send_oled1(self):
        text = self.editOled1.text()
        cmd = f"OL1 {text}"
        self.send_cmd(cmd)

    def send_oled2(self):
        text = self.editOled2.text()
        cmd = f"OL2 {text}"
        self.send_cmd(cmd)

    # ------------------------------------------------------------------
    # Đọc ADS
    # ------------------------------------------------------------------
    def load_ads(self):
        self.send_cmd("ADS")

    # ------------------------------------------------------------------
    # Ô nhập lệnh trực tiếp
    # ------------------------------------------------------------------
    def send_custom_cmd(self):
        cmd = self.editCmd.text().strip()
        if cmd:
            self.send_cmd(cmd)

    # ------------------------------------------------------------------
    # Điều khiển I/O SPARE
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
            "  READ            → STATUS;ADC=a1,a2,a3,a4;S=s1..s16;\n"
            "  ADS             → ADS;A0=xxxx;A1=yyyy;\n\n"
            "Relay (16 kênh: R1..R16):\n"
            "  R1 ON / R1 OFF\n"
            "  R2 ON / R2 OFF\n"
            "  R3 ON / R3 OFF\n"
            "  ...\n"
            "  R16 ON / R16 OFF\n\n"
            "LED on-board (SPARE2 – GPIO2):\n"
            "  LED ON\n"
            "  LED OFF\n\n"
            "WS2812 (1 LED RGB):\n"
            "  RGB R,G,B       (R,G,B: 0–255)\n"
            "    VD: RGB 255,0,128\n\n"
            "OLED (2 dòng):\n"
            "  OL1 <text>      → ghi dòng 1\n"
            "  OL2 <text>      → ghi dòng 2\n"
            "    VD: OL1 Hung Tran\n"
            "    VD: OL2 -SEHC-\n\n"
            "I/O SPARE (output):\n"
            "   SIO1 ON / SIO1 OFF\n"
            "   SIO2 ON / SIO2 OFF\n"
            "   SIO3 ON / SIO3 OFF\n"
            "   SIO4 ON / SIO4 OFF\n"
            "   SIO5 ON / SIO5 OFF\n"
            "   SIO6 ON / SIO6 OFF\n\n"
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

        # Reset tối đa 16 sensor S1..S16 (tùy UI/firmware)
        for i in range(1, 17):
            lbl = getattr(self, f"labelS{i}", None)
            if lbl is not None:
                lbl.setText("-")

        # Reset ADS0..ADS3 nếu có
        for i in range(0, 4):
            lbl = getattr(self, f"labelADS{i}", None)
            if lbl is not None:
                lbl.setText("-")

    def log(self, text: str):
        self.logg.append(text)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = PSWKitWindow()
    win.setWindowTitle("ESP32 KIT Tester (Dashboard)")
    win.show()
    sys.exit(app.exec_())
