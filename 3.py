import sys
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QGraphicsView, QGraphicsScene,
    QGraphicsRectItem, QGraphicsSimpleTextItem, QToolBar, QAction,
    QGraphicsPathItem
)
from PyQt5.QtGui import QBrush, QPen, QPainter, QPainterPath
from PyQt5.QtCore import Qt, QRectF, QPointF
from PyQt5.QtWidgets import QGraphicsItem


# ===============================
# GateItem：邏輯閘（含 pin 與邏輯狀態）
# ===============================

class GateItem(QGraphicsRectItem):
    """
    一個可拖曳的邏輯閘物件，上面會畫出 input / output pin。
    類型：
      AND / OR : 2 input, 1 output
      NOT      : 1 input, 1 output
      IN       : 0 input, 1 output（訊號來源，可 0/1）
      OUT      : 1 input, 0 output（顯示用）
    """

    def __init__(self, gate_type="AND", x=0, y=0, w=80, h=50):
        super().__init__(0, 0, w, h)
        self.gate_type = gate_type
        self.w = w
        self.h = h

        # ---- pin 結構 ----
        if gate_type in ("AND", "OR"):
            self.input_count = 2
            self.has_output = True
        elif gate_type == "NOT":
            self.input_count = 1
            self.has_output = True
        elif gate_type == "IN":
            self.input_count = 0
            self.has_output = True
        elif gate_type == "OUT":
            self.input_count = 1
            self.has_output = False
        else:
            self.input_count = 2
            self.has_output = True

        # wires
        self.connected_wires = []
        self.input_wires = [None] * self.input_count
        self.output_wires = []

        # 邏輯值
        self.value = False      # IN gate 用
        self.out_value = False  # 此 gate 的輸出值（模擬時更新）

        # 外觀
        self.setBrush(QBrush(Qt.white))
        self.setPen(QPen(Qt.black, 2))
        self.setFlags(
            QGraphicsRectItem.ItemIsMovable |
            QGraphicsRectItem.ItemIsSelectable |
            QGraphicsRectItem.ItemSendsGeometryChanges
        )

        # 顯示文字
        text = gate_type
        if gate_type in ("IN", "OUT"):
            text = f"{gate_type} 0"
        self.label = QGraphicsSimpleTextItem(text, self)
        b = self.label.boundingRect()
        self.label.setPos((w - b.width()) / 2, (h - b.height()) / 2)

        # 起始位置
        self.setPos(x, y)

    # ---------- pin 幾何 ----------

    def get_input_pin_local_pos(self, index: int) -> QPointF:
        x = 0
        step = self.h / (self.input_count + 1)
        y = step * (index + 1)
        return QPointF(x, y)

    def get_output_pin_local_pos(self) -> QPointF:
        x = self.w
        y = self.h / 2
        return QPointF(x, y)

    def get_input_pin_scene_pos(self, index: int) -> QPointF:
        return self.mapToScene(self.get_input_pin_local_pos(index))

    def get_output_pin_scene_pos(self) -> QPointF:
        return self.mapToScene(self.get_output_pin_local_pos())

    def hit_test_pin(self, scene_pos: QPointF, radius: float = 8.0):
        """
        檢查滑鼠點擊是否在某個 pin 上。
        回傳:
           ("in", index) 或 ("out", 0)
           None = 沒有 hit
        """
        # input pins
        for i in range(self.input_count):
            p = self.get_input_pin_scene_pos(i)
            if (p - scene_pos).manhattanLength() <= radius:
                return ("in", i)

        # output pin（有才測）
        if self.has_output:
            p_out = self.get_output_pin_scene_pos()
            if (p_out - scene_pos).manhattanLength() <= radius:
                return ("out", 0)

        return None

    # ---------- 繪製：矩形 + pin ----------

    def paint(self, painter: QPainter, option, widget=None):
        # 先畫本體（用目前的 brush / pen）
        super().paint(painter, option, widget)

        painter.setBrush(Qt.black)
        r = 4

        # input pins
        for i in range(self.input_count):
            lp = self.get_input_pin_local_pos(i)
            painter.drawEllipse(lp,r,r)

        # output pin
        if self.has_output:
            lp_out = self.get_output_pin_local_pos()
            painter.drawEllipse(lp_out,r,r)

    # ---------- wire 相關 ----------

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            for w in self.connected_wires:
                w.update_path()
        return super().itemChange(change, value)

    def add_wire(self, wire):
        if wire not in self.connected_wires:
            self.connected_wires.append(wire)

    def remove_wire(self, wire):
        if wire in self.connected_wires:
            self.connected_wires.remove(wire)
        # 從 input / output 清掉
        for i, w in enumerate(self.input_wires):
            if w is wire:
                self.input_wires[i] = None
        if wire in self.output_wires:
            self.output_wires.remove(wire)

    def connect_input(self, index, wire):
        if not (0 <= index < self.input_count):
            return
        if self.input_wires[index] is not None and self.input_wires[index] is not wire:
            # 若原本有線，先斷開
            old = self.input_wires[index]
            old.disconnect()
        self.input_wires[index] = wire
        self.add_wire(wire)

    def connect_output(self, wire):
        if wire not in self.output_wires:
            self.output_wires.append(wire)
        self.add_wire(wire)

    # ---------- 互動：double click 切換 IN 值 ----------

    def mouseDoubleClickEvent(self, event):
        if self.gate_type == "IN":
            self.value = not self.value
            # 讓 GUI 立刻反映（文字 & 顏色）
            self.out_value = self.value
            self.update_display()
        super().mouseDoubleClickEvent(event)

    # ---------- 更新顯示（顏色 + 文字） ----------

    def update_display(self):
        # 決定顏色：True = 黃，False = 白
        val = self.out_value if self.gate_type != "IN" else self.value
        if val:
            self.setBrush(QBrush(Qt.yellow))
        else:
            self.setBrush(QBrush(Qt.white))

        # IN/OUT 顯示 0/1
        if self.gate_type in ("IN", "OUT"):
            bit = 1 if val else 0
            self.label.setText(f"{self.gate_type} {bit}")
        else:
            self.label.setText(self.gate_type)

        b = self.label.boundingRect()
        self.label.setPos((self.w - b.width()) / 2, (self.h - b.height()) / 2)


# ===============================
# WireItem：連接兩個 gate 的線
# ===============================

class WireItem(QGraphicsPathItem):
    """
    連接 start_gate 的 output pin → end_gate 的 input pin。
    """

    def __init__(self, start_gate: GateItem, start_kind: str, start_index: int):
        super().__init__()
        self.start_gate = start_gate
        self.start_kind = start_kind   # 應該是 "out"
        self.start_index = start_index

        self.end_gate = None
        self.end_kind = None           # 應該是 "in"
        self.end_index = None

        self.temp_end_pos = None       # 拉線時暫時終點

        self.setPen(QPen(Qt.black, 2))
        self.setZValue(-1)
        self.setFlags(
            QGraphicsItem.ItemIsSelectable |
            QGraphicsItem.ItemSendsGeometryChanges
        )

        # 先讓起點 gate 記住有這條線
        self.start_gate.add_wire(self)

        self.update_path()

    def set_temp_end_pos(self, pos: QPointF):
        self.temp_end_pos = pos
        self.update_path()

    def finalize_connection(self, end_gate: GateItem, end_kind: str, end_index: int):
        self.end_gate = end_gate
        self.end_kind = end_kind
        self.end_index = end_index
        self.temp_end_pos = None

        # 註冊到 gate
        if self.start_kind == "out":
            self.start_gate.connect_output(self)
        if self.end_kind == "in":
            self.end_gate.connect_input(self.end_index, self)

        self.update_path()

    def update_path(self):
        # 起點
        if self.start_kind == "out":
            start = self.start_gate.get_output_pin_scene_pos()
        else:
            start = self.start_gate.get_input_pin_scene_pos(self.start_index)

        # 終點
        if self.end_gate is not None:
            if self.end_kind == "in":
                end = self.end_gate.get_input_pin_scene_pos(self.end_index)
            else:
                end = self.end_gate.get_output_pin_scene_pos()
        else:
            end = self.temp_end_pos if self.temp_end_pos is not None else start

        path = QPainterPath()
        path.moveTo(start)
        path.lineTo(end)
        self.setPath(path)

    def has_gate(self, gate: GateItem) -> bool:
        return (gate is self.start_gate) or (gate is self.end_gate)

    def disconnect(self):
        if self.start_gate:
            self.start_gate.remove_wire(self)
        if self.end_gate:
            self.end_gate.remove_wire(self)


# ===============================
# View：處理拉線 & Delete
# ===============================

class LogicEditorView(QGraphicsView):
    """
    - 拖曳 Gate
    - 點 output pin → 拉線 → 點 input pin 完成
    - Delete 刪除 Gate / Wire
    """

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)

        self.drawing_wire = False
        self.current_wire = None  # type: WireItem | None

    def mousePressEvent(self, event):
        scene_pos = self.mapToScene(event.pos())

        if event.button() == Qt.LeftButton:
            hit_gate = None
            hit_kind = None
            hit_index = None

            # 找有沒有點到某個 Gate 的 pin
            for item in self.scene().items(scene_pos):
                if isinstance(item, GateItem):
                    res = item.hit_test_pin(scene_pos)
                    if res is not None:
                        hit_kind, hit_index = res
                        hit_gate = item
                        break

            if hit_gate is not None:
                # pin 被點到
                if not self.drawing_wire:
                    # 只允許從 output 開始拉線
                    if hit_kind == "out" and hit_gate.has_output:
                        self.drawing_wire = True
                        self.current_wire = WireItem(
                            start_gate=hit_gate,
                            start_kind=hit_kind,
                            start_index=hit_index
                        )
                        self.scene().addItem(self.current_wire)
                        self.current_wire.set_temp_end_pos(scene_pos)
                        return
                else:
                    # 正在拉線 → 嘗試完成連線（只能接到 input）
                    if (
                        self.current_wire is not None and
                        hit_kind == "in" and
                        hit_gate is not self.current_wire.start_gate
                    ):
                        self.current_wire.finalize_connection(
                            end_gate=hit_gate,
                            end_kind=hit_kind,
                            end_index=hit_index
                        )
                    else:
                        # 無效的終點 → 取消
                        self.current_wire.disconnect()
                        self.scene().removeItem(self.current_wire)
                    self.drawing_wire = False
                    self.current_wire = None
                    return
            else:
                # 點空白處，若正在拉線就取消
                if self.drawing_wire and self.current_wire is not None:
                    self.current_wire.disconnect()
                    self.scene().removeItem(self.current_wire)
                    self.current_wire = None
                    self.drawing_wire = False

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drawing_wire and self.current_wire is not None:
            scene_pos = self.mapToScene(event.pos())
            self.current_wire.set_temp_end_pos(scene_pos)
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            for item in list(self.scene().selectedItems()):
                if isinstance(item, WireItem):
                    item.disconnect()
                    self.scene().removeItem(item)
                elif isinstance(item, GateItem):
                    for w in list(item.connected_wires):
                        w.disconnect()
                        self.scene().removeItem(w)
                    self.scene().removeItem(item)
            return
        super().keyPressEvent(event)


# ===============================
# MainWindow：工具列 + 模擬
# ===============================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Python Logic Gate Editor (Mini Quartus Style)")
        self.resize(900, 600)

        # Scene & View
        self.scene = QGraphicsScene(self)
        self.scene.setSceneRect(0, 0, 2000, 2000)
        self.view = LogicEditorView(self.scene, self)
        self.setCentralWidget(self.view)

        # Toolbar
        toolbar = QToolBar("Gate Toolbar", self)
        self.addToolBar(toolbar)

        add_in_action = QAction("IN", self)
        add_in_action.triggered.connect(lambda: self.add_gate("IN"))
        toolbar.addAction(add_in_action)

        add_and_action = QAction("AND", self)
        add_and_action.triggered.connect(lambda: self.add_gate("AND"))
        toolbar.addAction(add_and_action)

        add_or_action = QAction("OR", self)
        add_or_action.triggered.connect(lambda: self.add_gate("OR"))
        toolbar.addAction(add_or_action)

        add_not_action = QAction("NOT", self)
        add_not_action.triggered.connect(lambda: self.add_gate("NOT"))
        toolbar.addAction(add_not_action)

        add_out_action = QAction("OUT", self)
        add_out_action.triggered.connect(lambda: self.add_gate("OUT"))
        toolbar.addAction(add_out_action)

        toolbar.addSeparator()

        simulate_action = QAction("Simulate", self)
        simulate_action.triggered.connect(self.simulate_circuit)
        toolbar.addAction(simulate_action)

        # 初始擺幾個 Gate
        self.add_gate("IN", x=50, y=150)
        self.add_gate("IN", x=50, y=250)
        self.add_gate("AND", x=250, y=200)
        self.add_gate("OUT", x=450, y=220)

    def add_gate(self, gate_type, x=50, y=50):
        gate = GateItem(gate_type=gate_type, x=x, y=y)
        self.scene.addItem(gate)

    # ---------- 邏輯模擬 ----------

    def simulate_circuit(self):
        # 取得所有 gate
        gates = [item for item in self.scene.items() if isinstance(item, GateItem)]

        # 初始輸出值
        for g in gates:
            g.out_value = None
            if g.gate_type == "IN":
                g.out_value = g.value  # IN 的輸出就是自己的 value

        max_iters = 10  # 簡單反覆更新，避免陷入循環

        for _ in range(max_iters):
            changed = False
            for g in gates:
                old = g.out_value

                if g.gate_type == "IN":
                    new = g.value

                else:
                    inputs = []
                    for i in range(g.input_count):
                        w = g.input_wires[i]
                        if w is not None and w.start_gate is not None:
                            src = w.start_gate
                            v = src.out_value
                            if v is None:
                                v = False
                        else:
                            v = False
                        inputs.append(v)

                    if g.gate_type == "AND":
                        new = all(inputs) if inputs else False
                    elif g.gate_type == "OR":
                        new = any(inputs) if inputs else False
                    elif g.gate_type == "NOT":
                        new = (not inputs[0]) if inputs else False
                    elif g.gate_type == "OUT":
                        new = inputs[0] if inputs else False
                    else:
                        new = False

                if new != old:
                    g.out_value = new
                    changed = True

            if not changed:
                break

        # 更新顯示
        for g in gates:
            g.update_display()


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
