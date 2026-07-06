import os
import sys
from collections import deque

import serial
import serial.tools.list_ports

from PyQt5 import uic
from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QSlider,
    QMessageBox,
    QGraphicsOpacityEffect,
    QRadioButton,
    QWidget,
    QVBoxLayout,
)

import pyqtgraph as pg

APP_VERSION = "Ver 10"
APP_DATE    = "Jun-26"
APP_AUTHOR  = "PIC. songhung.tr"


def resource_path(relative_path: str) -> str:
    """
    Trả về đường dẫn thực tế của file resource (VD: dashboard_2.ui),
    dùng được cả khi chạy .py bình thường và khi đóng gói PyInstaller.
    """
    if hasattr(sys, "_MEIPASS"):
        base_path = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


class PSWKitWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        # Load giao diện từ file .ui
        uic.loadUi(resource_path("dashboard_2.ui"), self)

        self.actionAbout.triggered.connect(self.show_about_message)

        # Cố định kích thước cửa sổ
        self.setFixedSize(1274, 876)

        # Đặt icon cho cửa sổ (title bar + taskbar)
        self.setWindowIcon(QIcon(resource_path("psw.ico")))

        # ===== Biến trạng thái =====
        # LED on-board (SPARE2) – hiện đang không dùng nút
        self.led_on = False

        # 16 relay output (R1..R16)
        self.relay_state = {i: False for i in range(1, 17)}

        # Đã nhận KIT=... sau INFO hay chưa
        self.handshake_ok = False

        # 6 ngõ I/O SPARE (SIO1..SIO6)
        self.sio_state = {i: False for i in range(1, 7)}

        # FIX #1: Thay command_lock bằng command_queue để không drop lệnh.
        # Lệnh được xếp hàng và gửi tuần tự, cách nhau CMD_INTERVAL ms.
        self._cmd_queue: deque = deque()
        self._cmd_busy = False
        CMD_INTERVAL = 120  # ms

        self._cmd_flush_timer = QTimer()
        self._cmd_flush_timer.setInterval(CMD_INTERVAL)
        self._cmd_flush_timer.setSingleShot(True)
        self._cmd_flush_timer.timeout.connect(self._flush_cmd_queue)

        # Serial manager (tách logic Serial khỏi UI)
        self.serial_manager = SerialManager(line_callback=self.handle_serial_line)

        # ===== Gắn signal cho các nút chính =====
        self.btnRefresh.clicked.connect(self.refresh_ports)
        self.btnConnect.clicked.connect(self.toggle_connect)

        # Relay buttons R1..R16
        self.relay_buttons = {}
        for i in range(1, 17):
            btn = getattr(self, f"btnR{i}", None)
            if btn is not None:
                self.relay_buttons[i] = btn
                btn.clicked.connect(
                    lambda _checked, idx=i, b=btn: self.toggle_relay(idx, b)
                )

        self.btnBuz.clicked.connect(lambda: self.send_cmd("BUZ"))
        # self.btnLed.clicked.connect(self.toggle_led)  # mở nếu cần
        self.btnRead.clicked.connect(lambda: self.send_cmd("READ"))

        # ===== FET (QRadioButton named "fet") =====
        # Checked  -> send "FET ON"
        # Unchecked-> send "FET OFF"
        self.radioFet = getattr(self, "fet", None)
        if isinstance(self.radioFet, QRadioButton):
            # allow uncheck (radio default is auto-exclusive)
            self.radioFet.setAutoExclusive(False)
            self.radioFet.toggled.connect(self.on_fet_toggled)
        elif self.radioFet is not None:
            self.log(f"WARNING: fet widget is not QRadioButton (type={type(self.radioFet)})")

        self.btnClean.clicked.connect(lambda: self.logg.clear())

        # Auto READ
        self.checkAutoRead.stateChanged.connect(self.on_auto_read_changed)

        # ===== Điều khiển OLED (2 dòng) =====
        self.btnOled1.clicked.connect(self.send_oled1)
        self.btnOled2.clicked.connect(self.send_oled2)

        # ===== Điều khiển ADS1115 (A0, A1, A2) =====
        self.btnAdsLoad.clicked.connect(self.load_ads)

        # ===== Ô nhập lệnh trực tiếp =====
        self.btnCmdSend.clicked.connect(self.send_custom_cmd)
        self.editCmd.returnPressed.connect(self.send_custom_cmd)

        # ===== RS485: QLineEdit "RS485_2" + QPushButton "Send_485" =====
        self._rs485_widgets_ok = False
        _btn_rs  = getattr(self, "Send_485", None)
        _edit_rs = getattr(self, "RS485_2",  None)
        if _btn_rs is not None and _edit_rs is not None:
            _btn_rs.clicked.connect(self.send_rs485_cmd)
            _edit_rs.returnPressed.connect(self.send_rs485_cmd)
            self._rs485_widgets_ok = True
        else:
            self.log("RS485 widgets (Send_485 / RS485_2) not found in UI.")

        # ===== Help / API =====
        self.btnHelp.clicked.connect(self.show_help)

        # ===== I/O SPARE (SIO1..SIO6) =====
        for i in range(1, 7):
            cb = getattr(self, f"checkSIO{i}", None)
            if cb is not None:
                cb.stateChanged.connect(lambda state, idx=i: self.set_sio(idx, state))

        # ===== Chọn loại board (ESP_IO_Ver2 / ESP_IO_Ver3 / B8M / B16M / ...) =====
        self.comboBox.currentTextChanged.connect(self.update_relay_ui_for_board)
        self.comboBox.currentTextChanged.connect(self.update_sensor_ui_for_board)

        # ===== Timer đọc Serial =====
        self.timer = QTimer()
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.read_serial)

        # ===== Timer Auto READ (gửi READ định kỳ) =====
        self.auto_timer = QTimer()
        self.auto_timer.setInterval(500)
        self.auto_timer.timeout.connect(self.auto_read_tick)

        # ===== Slider RGB cho WS2812 =====
        self.sliderR = self.findChild(QSlider, "sliderR")
        self.sliderG = self.findChild(QSlider, "sliderG")
        self.sliderB = self.findChild(QSlider, "sliderB")

        for s in (self.sliderR, self.sliderG, self.sliderB):
            if isinstance(s, QSlider):
                s.setMinimum(0)
                s.setMaximum(255)
            elif s is not None:
                self.log(f"WARNING: {s.objectName()} không phải QSlider (type={type(s)})")
            else:
                self.log("WARNING: Không tìm thấy sliderR / sliderG / sliderB trong .ui")

        if isinstance(self.sliderR, QSlider):
            self.sliderR.valueChanged.connect(self.update_rgb_labels)
            self.sliderR.sliderReleased.connect(self.send_rgb_from_sliders)
        if isinstance(self.sliderG, QSlider):
            self.sliderG.valueChanged.connect(self.update_rgb_labels)
            self.sliderG.sliderReleased.connect(self.send_rgb_from_sliders)
        if isinstance(self.sliderB, QSlider):
            self.sliderB.valueChanged.connect(self.update_rgb_labels)
            self.sliderB.sliderReleased.connect(self.send_rgb_from_sliders)

        # ===== FIX #11: Plot ADC1 (Realtime) =====
        # plotWidget trong .ui là QWidget placeholder; nhúng pg.PlotWidget vào layout của nó.
        placeholder: QWidget = self.plotWidget
        layout = QVBoxLayout(placeholder)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot = pg.PlotWidget()
        layout.addWidget(self.plot)

        self.plot.setLabel("left", "ADC1 Value")
        self.plot.setLabel("bottom", "Samples")
        self.plot.showGrid(x=True, y=True)
        self.plot_data: list = []
        self.max_points = 200
        self.curve = self.plot.plot([], [])

        # ===== Khởi tạo ban đầu =====
        self.refresh_ports()
        self.reset_status_labels()
        self.update_rgb_labels()
        self.update_conn_label(False)
        self.update_all_relay_labels()
        # Khóa toàn bộ control cho tới khi connect
        self.set_controls_enabled(False)

        # Khởi tạo UI relay/sensor theo loại board đang chọn
        self.update_relay_ui_for_board(self.comboBox.currentText())
        self.update_sensor_ui_for_board(self.comboBox.currentText())

    # ------------------------------------------------------------------
    # COM port
    # ------------------------------------------------------------------
    def refresh_ports(self):
        """Lấy danh sách cổng từ SerialManager và đổ vào comboPort."""
        self.comboPort.clear()
        ports = self.serial_manager.list_ports()
        for dev in ports:
            self.comboPort.addItem(dev)
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
        """state = 0 (unchecked), 2 (checked)"""
        if state == 2:
            if not self.auto_timer.isActive():
                self.auto_timer.start()
                self.log("Auto READ ON")
        else:
            if self.auto_timer.isActive():
                self.auto_timer.stop()
                self.log("Auto READ OFF")

    def auto_read_tick(self):
        """Gọi định kỳ bởi self.auto_timer."""
        if self.serial_manager.is_connected():
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
    # FIX #4 & #10: set_controls_enabled – thêm RS485 widgets,
    # đồng bộ với max_relays của board hiện tại.
    # ------------------------------------------------------------------
    def set_controls_enabled(self, enabled: bool):
        """Khóa toàn bộ control điều khiển KIT khi chưa connect."""
        board = self.comboBox.currentText()
        max_relays = self._max_relays_for_board(board)

        # Relay buttons – kênh trong range board theo `enabled`; kênh ngoài luôn False
        for i in range(1, 17):
            btn = getattr(self, f"btnR{i}", None)
            if btn is not None:
                btn.setEnabled(enabled and i <= max_relays)

        # SIO checkboxes – FW chỉ hỗ trợ SIO1..SIO4; SIO5/SIO6 luôn disabled
        for i in range(1, 7):
            cb = getattr(self, f"checkSIO{i}", None)
            if cb is not None:
                cb.setEnabled(enabled and i <= 4)

        # Các nút / checkbox liên quan tới lệnh
        for name in [
            "btnBuz",
            "btnRead",
            "btnOled1",
            "btnOled2",
            "btnAdsLoad",
            "btnCmdSend",
            "checkAutoRead",
        ]:
            w = getattr(self, name, None)
            if w is not None:
                w.setEnabled(enabled)

        # Ô nhập lệnh custom
        if hasattr(self, "editCmd") and self.editCmd is not None:
            self.editCmd.setEnabled(enabled)

        # RS485 widgets
        if self._rs485_widgets_ok:
            for name in ("Send_485", "RS485_2"):
                w = getattr(self, name, None)
                if w is not None:
                    w.setEnabled(enabled)

        # Slider RGB
        for s in (self.sliderR, self.sliderG, self.sliderB):
            if isinstance(s, QSlider):
                s.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Relay label helpers
    # ------------------------------------------------------------------
    def update_relay_label(self, idx: int, state: bool):
        """Cập nhật labelR{idx}State theo trạng thái relay (ON/OFF)."""
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
        """Sync tất cả labelR1State..labelR16State với self.relay_state."""
        for i, st in self.relay_state.items():
            self.update_relay_label(i, st)

    # ------------------------------------------------------------------
    # FIX #10: Tách helper _max_relays_for_board để dùng chung
    # ------------------------------------------------------------------
    @staticmethod
    def _max_relays_for_board(board: str) -> int:
        if board in ("B8M", "A8S", "KIT", "Insert"):
            return 8
        if board == "B16M":
            return 16
        if board in ("ESP_IO_Ver3", "ESP_IO_Ver2", "A4S"):
            return 4
        return 16  # fallback

    def update_relay_ui_for_board(self, board: str):
        """
        Bật/tắt các nút R1..R16 và labelR1State..labelR16State
        tùy theo loại board chọn trong comboBox.
        Trạng thái connected được giữ nguyên – set_controls_enabled đồng bộ lại.
        """
        max_relays = self._max_relays_for_board(board)
        is_connected = self.serial_manager.is_connected()

        for i in range(1, 17):
            btn = getattr(self, f"btnR{i}", None)
            lbl = getattr(self, f"labelR{i}State", None)
            in_range = (i <= max_relays)

            for w in (btn, lbl):
                if w is None:
                    continue
                w.setVisible(True)
                if in_range:
                    w.setEnabled(is_connected)
                    eff = w.graphicsEffect()
                    if isinstance(eff, QGraphicsOpacityEffect):
                        eff.setOpacity(1.0)
                else:
                    w.setEnabled(False)
                    eff = w.graphicsEffect()
                    if not isinstance(eff, QGraphicsOpacityEffect):
                        eff = QGraphicsOpacityEffect(w)
                        w.setGraphicsEffect(eff)
                    eff.setOpacity(0.2)

    def update_sensor_ui_for_board(self, board: str):
        """
        Làm sáng / mờ các labelS1..labelS16 tùy loại board.
        - ESP_IO_Ver3, ESP_IO_Ver2, A4S → S1..S5
        - A8S, KIT, B8M, Insert         → S1..S8
        - B16M                           → S1..S16
        - Khác                           → S1..S16 (fallback)
        """
        if board in ("ESP_IO_Ver3", "ESP_IO_Ver2", "A4S"):
            max_sensors = 5
        elif board in ("A8S", "KIT", "B8M", "Insert"):
            max_sensors = 8
        elif board == "B16M":
            max_sensors = 16
        else:
            max_sensors = 16

        for i in range(1, 17):
            lbl = getattr(self, f"labelS{i}", None)
            if lbl is None:
                continue
            enabled = (i <= max_sensors)
            if enabled:
                lbl.setEnabled(True)
                eff = lbl.graphicsEffect()
                if isinstance(eff, QGraphicsOpacityEffect):
                    eff.setOpacity(1.0)
            else:
                lbl.setEnabled(False)
                eff = lbl.graphicsEffect()
                if not isinstance(eff, QGraphicsOpacityEffect):
                    eff = QGraphicsOpacityEffect(lbl)
                    lbl.setGraphicsEffect(eff)
                eff.setOpacity(0.2)

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

    def send_rgb_from_sliders(self):
        r = self.sliderR.value() if isinstance(self.sliderR, QSlider) else 0
        g = self.sliderG.value() if isinstance(self.sliderG, QSlider) else 0
        b = self.sliderB.value() if isinstance(self.sliderB, QSlider) else 0
        self.send_cmd(f"RGB {r},{g},{b}")

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
                self.set_controls_enabled(False)
                return

            ok, err = self.serial_manager.connect(port, 115200, timeout=0.1)
            if ok:
                self.log(f"Connected to {port}")
                self.btnConnect.setText("Disconnect")
                self.timer.start()
                self.update_conn_label(True)
                self.set_controls_enabled(True)
                # Sau khi connect, gửi INFO để đọc KIT=...
                self.send_cmd("INFO")
            else:
                self.log(f"Connect failed: {err}")
                self.btnConnect.setChecked(False)
                self.update_conn_label(False)
                self.set_controls_enabled(False)
        else:
            # Ngắt kết nối
            self.timer.stop()
            self.auto_timer.stop()
            self.checkAutoRead.setChecked(False)

            self.serial_manager.disconnect()
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

            self.set_controls_enabled(False)

    # ------------------------------------------------------------------
    # FIX #1: Gửi lệnh xuống ESP32 bằng queue – không drop lệnh
    # ------------------------------------------------------------------
    def send_cmd(self, cmd: str):
        """
        Xếp lệnh vào hàng đợi. Lệnh được gửi tuần tự, cách nhau
        CMD_INTERVAL ms (cấu hình qua _cmd_flush_timer).
        Nếu không connected, log và bỏ qua.
        """
        if not self.serial_manager.is_connected():
            self.log("Not connected.")
            return
        self._cmd_queue.append(cmd)
        if not self._cmd_busy:
            self._flush_cmd_queue()

    def _flush_cmd_queue(self):
        """Lấy 1 lệnh từ queue và gửi; lên lịch gửi tiếp nếu queue còn lệnh."""
        if not self._cmd_queue:
            self._cmd_busy = False
            return

        self._cmd_busy = True
        cmd = self._cmd_queue.popleft()

        if not self.serial_manager.is_connected():
            # Mất kết nối trong lúc chờ → xóa hết queue
            self._cmd_queue.clear()
            self._cmd_busy = False
            return

        try:
            self.serial_manager.send_line(cmd)
            self.log(f">>> {cmd}")
        except Exception as e:
            self.log(f"Send error: {e}")
            self._cmd_queue.clear()
            self._cmd_busy = False
            return

        if self._cmd_queue:
            self._cmd_flush_timer.start()
        else:
            self._cmd_busy = False

    # ------------------------------------------------------------------
    # FET control (QRadioButton "fet")
    # ------------------------------------------------------------------
    def on_fet_toggled(self, checked: bool):
        self.send_cmd("FET ON" if checked else "FET OFF")

    # ------------------------------------------------------------------
    # Callback nhận từng dòng serial từ SerialManager
    # ------------------------------------------------------------------
    def handle_serial_line(self, line: str):
        """Được SerialManager gọi cho mỗi dòng nhận được."""
        if line.startswith("!SERIAL_ERROR:"):
            self.log(line)
            self.handle_serial_disconnect()
            return
        self.log(f"<<< {line}")
        self.parse_line(line)

    # ------------------------------------------------------------------
    # Đọc Serial (poll từ SerialManager)
    # ------------------------------------------------------------------
    def read_serial(self):
        """Hàm này được timer gọi mỗi 100 ms để đọc dữ liệu serial."""
        self.serial_manager.poll()

    # ------------------------------------------------------------------
    # FIX #3: handle_serial_disconnect – tránh re-entrant qua setChecked
    # ------------------------------------------------------------------
    def handle_serial_disconnect(self):
        """Được gọi khi COM bị rút / lỗi serial: auto về trạng thái DISCONNECTED."""
        self.log("Serial disconnected (COM removed?)")

        # Dừng timer trước để không tiếp tục poll
        self.timer.stop()
        self.auto_timer.stop()
        self._cmd_queue.clear()
        self._cmd_busy = False

        # Cleanup trực tiếp thay vì gọi lại toggle_connect (tránh re-entrant)
        self.serial_manager.disconnect()
        self.handshake_ok = False
        self.btnConnect.setText("Connect")

        # blockSignals để tránh toggle_connect bị trigger lại
        self.btnConnect.blockSignals(True)
        self.btnConnect.setChecked(False)
        self.btnConnect.blockSignals(False)

        self.checkAutoRead.setChecked(False)
        for i in range(1, 7):
            cb = getattr(self, f"checkSIO{i}", None)
            if cb is not None:
                cb.setChecked(False)
        self.sio_state = {i: False for i in range(1, 7)}

        self.update_conn_label(False)
        self.set_controls_enabled(False)

    # ------------------------------------------------------------------
    # Parse dữ liệu trả về từ ESP32
    # ------------------------------------------------------------------
    def parse_line(self, line: str):
        # Thông tin board: "KIT=B16M;FW=1.0;" hoặc "B16M;FW=1.0;"
        if line.startswith("KIT=") or (
            ";FW=" in line
            and not line.startswith("STATUS;")
            and not line.startswith("ADS;")
        ):
            kit_name = ""
            fw_ver = ""
            try:
                parts = [p for p in line.split(";") if p]
                for p in parts:
                    if p.startswith("KIT="):
                        kit_name = p.split("=", 1)[1]
                    elif p.startswith("FW="):
                        fw_ver = p.split("=", 1)[1]
                    else:
                        if not kit_name:
                            kit_name = p

                if kit_name:
                    self.log(f"Detected KIT={kit_name}, FW={fw_ver}")
                    idx = self.comboBox.findText(kit_name)
                    if idx != -1:
                        self.comboBox.setCurrentIndex(idx)
                    else:
                        self.log(f"Board '{kit_name}' not found in comboBox list.")
            except Exception as e:
                self.log(f"Parse KIT line error: {e}")

            if not self.handshake_ok:
                self.handshake_ok = True
                self.send_cmd("BUZ")
            return

        # RS485_RECV=<data> – ESP32 forward data nhận từ bus RS485 lên PC
        if line.startswith("RS485_RECV="):
            data = line[len("RS485_RECV="):]
            self.log(f"[RS485 RX] {data}")
            return

        # STATUS;ADC=v1,v2,v3;S=s0..s7;BTN=b1,b2,b3;
        # ESP32 FW trả 3 kênh ADC (A1,A2,A3) + 8 sensor + 3 button
        if line.startswith("STATUS;"):
            try:
                parts = line.split(";")
                adc_vals = None
                s_vals   = None
                btn_vals = None

                for p in parts:
                    if p.startswith("ADC="):
                        adc_str = p[4:]
                        if adc_str:
                            adc_vals = [int(x) for x in adc_str.split(",") if x != ""]
                    elif p.startswith("S="):
                        s_str = p[2:]
                        if s_str:
                            s_vals = [int(x) for x in s_str.split(",") if x != ""]
                    elif p.startswith("BTN="):
                        btn_str = p[4:]
                        if btn_str:
                            btn_vals = [int(x) for x in btn_str.split(",") if x != ""]

                # FW gửi 3 kênh: ADC1, ADC2, ADC3 (không có ADC4)
                if adc_vals:
                    for j, lname in enumerate(
                        ("labelADC1", "labelADC2", "labelADC3")
                    ):
                        if j < len(adc_vals):
                            lbl = getattr(self, lname, None)
                            if lbl is not None:
                                lbl.setText(str(adc_vals[j]))
                    # Ẩn / reset labelADC4 vì FW không cung cấp kênh này
                    lbl4 = getattr(self, "labelADC4", None)
                    if lbl4 is not None:
                        lbl4.setText("N/A")
                    self.update_adc_plot(adc_vals[0])

                if s_vals:
                    for i, val in enumerate(s_vals, start=1):
                        lbl = getattr(self, f"labelS{i}", None)
                        if lbl is not None:
                            lbl.setText(str(val))

                # BTN=b1,b2,b3 → labelBTN1, labelBTN2, labelBTN3 (nếu có trong UI)
                if btn_vals:
                    for i, val in enumerate(btn_vals, start=1):
                        lbl = getattr(self, f"labelBTN{i}", None)
                        if lbl is not None:
                            lbl.setText("ON" if val else "OFF")

            except Exception as e:
                self.log(f"Parse STATUS error: {e}")

        # ADS;A0=xxxx;A1=yyyy;A2=zzzz;
        # ESP32 FW trả 3 kênh ADS (A0, A1, A2)
        elif line.startswith("ADS;"):
            try:
                parts = line.split(";")
                for p in parts:
                    if p.startswith("A0="):
                        lbl = getattr(self, "labelADS0", None)
                        if lbl is not None:
                            lbl.setText(str(int(p[3:])))
                    elif p.startswith("A1="):
                        lbl = getattr(self, "labelADS1", None)
                        if lbl is not None:
                            lbl.setText(str(int(p[3:])))
                    elif p.startswith("A2="):
                        lbl = getattr(self, "labelADS2", None)
                        if lbl is not None:
                            lbl.setText(str(int(p[3:])))
            except Exception as e:
                self.log(f"Parse ADS error: {e}")

    # ------------------------------------------------------------------
    # Gửi text cho OLED
    # ------------------------------------------------------------------
    def send_oled1(self):
        # FIX #5: guard text rỗng
        text = self.editOled1.text().strip()
        if not text:
            self.log("OLED1: Không có text để gửi.")
            return
        self.send_cmd(f"OL1 {text}")

    def send_oled2(self):
        # FIX #5: guard text rỗng
        text = self.editOled2.text().strip()
        if not text:
            self.log("OLED2: Không có text để gửi.")
            return
        self.send_cmd(f"OL2 {text}")

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

    def send_rs485_cmd(self):
        """
        Lấy text từ QLineEdit RS485_2, wrap thành lệnh 'RS485 <data>'
        rồi gửi xuống ESP32 qua serial.
        ESP32 sẽ forward data ra bus RS485 (UART2 pin 25/26).
        """
        edit_rs = getattr(self, "RS485_2", None)
        if edit_rs is None:
            self.log("[RS485] Widget RS485_2 not found in UI.")
            return
        data = edit_rs.text().strip()
        if not data:
            self.log("[RS485] Không có data để gửi.")
            return
        self.log(f"[RS485 TX] {data}")
        self.send_cmd(f"RS485 {data}")

    # ------------------------------------------------------------------
    # Điều khiển I/O SPARE
    # ------------------------------------------------------------------
    def set_sio(self, idx: int, state: int):
        """state: 0 = unchecked (OFF), 2 = checked (ON)"""
        on = (state != 0)
        self.sio_state[idx] = on
        self.send_cmd(f"SIO{idx} {'ON' if on else 'OFF'}")

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
            "  INFO            → 'B16M;FW=1.0'\n\n"
            "Đọc trạng thái:\n"
            "  READ  → STATUS;ADC=A1,A2,A3;S=S0..S7;BTN=B1,B2,B3;\n"
            "  ADS   → ADS;A0=xxxx;A1=yyyy;A2=zzzz;\n\n"
            "Relay (8 kênh qua PCF8574: R1..R8):\n"
            "  R1 ON / R1 OFF  ...  R8 ON / R8 OFF\n\n"
            "LED on-board (SPARE2 – GPIO2):\n"
            "  LED ON / LED OFF\n\n"
            "WS2812 (1 LED RGB):\n"
            "  RGB R,G,B       (R,G,B: 0–255)\n"
            "    VD: RGB 255,0,128\n\n"
            "OLED (2 dòng):\n"
            "  OL1 <text>      → ghi dòng 1\n"
            "  OL2 <text>      → ghi dòng 2\n\n"
            "I/O SPARE (output – GPIO):\n"
            "  SIO1..SIO4  ON / OFF  (SIO5/6 không hỗ trợ)\n\n"
            "RS485 (MAX13487, UART2 pin TX=25 RX=26):\n"
            "  RS485 <data>    → ESP32 gửi <data> ra bus RS485\n"
            "  Nhận:           → ESP32 tự động gửi RS485_RECV=<data> lên app\n\n"
            "Gợi ý test bằng Docklight / terminal:\n"
            "  PING\\r\\n  → PONG\n"
            "  READ\\r\\n  → STATUS;...\n"
            "  ADS\\r\\n   → ADS;A0=...;A1=...;\n"
        )
        QMessageBox.information(self, "Help – Serial API", text)

    # ------------------------------------------------------------------
    # Reset labels
    # ------------------------------------------------------------------
    def reset_status_labels(self):
        # ADC1..3 từ FW; ADC4 không có → hiện "N/A"
        for name in ("labelADC1", "labelADC2", "labelADC3"):
            lbl = getattr(self, name, None)
            if lbl is not None:
                lbl.setText("-")
        lbl4 = getattr(self, "labelADC4", None)
        if lbl4 is not None:
            lbl4.setText("N/A")

        for i in range(1, 17):
            lbl = getattr(self, f"labelS{i}", None)
            if lbl is not None:
                lbl.setText("-")

        # ADS: A0, A1, A2 (FW trả 3 kênh)
        for i in range(0, 3):
            lbl = getattr(self, f"labelADS{i}", None)
            if lbl is not None:
                lbl.setText("-")

        # BTN1..3
        for i in range(1, 4):
            lbl = getattr(self, f"labelBTN{i}", None)
            if lbl is not None:
                lbl.setText("-")

    def log(self, text: str):
        self.logg.append(text)

    # FIX #6: About đúng version
    def show_about_message(self):
        QMessageBox.information(
            self, "About",
            f"{APP_VERSION}\n{APP_DATE}\n{APP_AUTHOR}"
        )


# ======================================================================
class SerialManager:
    """
    Lớp chuyên quản lý Serial: connect / disconnect / send / poll.
    Dùng callback để trả dữ liệu từng dòng về cho UI.
    """

    def __init__(self, line_callback=None):
        self.ser = None
        self.line_callback = line_callback

    def list_ports(self):
        """Trả về danh sách tên cổng COM (string)."""
        return [p.device for p in serial.tools.list_ports.comports()]

    def is_connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def connect(self, port: str, baudrate: int = 115200, timeout: float = 0.1):
        """Mở cổng serial. Trả về (ok: bool, err: Optional[str])"""
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
        try:
            self.ser = serial.Serial(port, baudrate, timeout=timeout)
            return True, None
        except Exception as e:
            self.ser = None
            return False, str(e)

    def disconnect(self):
        """Đóng cổng serial nếu đang mở."""
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    def send_line(self, cmd: str):
        """
        Gửi 1 dòng lệnh, tự thêm \\n ở cuối.
        Ném RuntimeError nếu chưa kết nối.
        """
        if not self.is_connected():
            raise RuntimeError("Not connected")
        self.ser.write((cmd + "\n").encode("utf-8"))

    def poll(self):
        """
        Đọc tất cả dữ liệu đang có trong buffer và gọi line_callback
        cho từng dòng (đã decode, strip).
        """
        if not self.is_connected():
            return
        try:
            while self.ser.in_waiting > 0:
                raw = self.ser.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                if self.line_callback is not None:
                    self.line_callback(line)
        except Exception as e:
            if self.line_callback is not None:
                self.line_callback(f"!SERIAL_ERROR: {e}")
            self.disconnect()


# ======================================================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = PSWKitWindow()
    win.setWindowTitle("ESP32 KIT Tester (Dashboard)")
    win.show()
    sys.exit(app.exec_())