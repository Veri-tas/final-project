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
# GateItem：邏輯閘（含 pin）
# ===============================

class GateItem(QGraphicsRectItem):
    """
    一個可拖曳的邏輯閘物件，上面會畫出 input / output 的 pin。
    目前簡化：
      AND / OR : 2 input, 1 output
      NOT      : 1 input, 1 output
    """

    def __init__(self, gate_type="AND", x=0, y=0, w=80, h=50):
        super().__init__(0, 0, w, h)
        self.gate_type = gate_type
        self.w = w
        self.h = h

        # 依 gate type 設定 input 數
        if gate_type in ("AND", "OR"):
            self.input_count = 2
        elif gate_type == "NOT":
            self.input_count = 1
        else:
            self.input_count = 2

        # 紀錄連到自己的 WireItem
        self.connected_wires = []

        # 外觀
        self.setBrush(QBrush(Qt.white))
        self.setPen(QPen(Qt.black, 2))
        self.setFlags(
            QGraphicsRectItem.ItemIsMovable |
            QGraphicsRectItem.ItemIsSelectable |
            QGraphicsRectItem.ItemSendsGeometryChanges
        )

        # 中間顯示 gate 名字
        self.label = QGraphicsSimpleTextItem(gate_type, self)
        b = self.label.boundingRect()
        self.label.setPos((w - b.width()) / 2, (h - b.height()) / 2)

        # 起始位置
        self.setPos(x, y)

    # --- pins 幾何 ---

    def get_input_pin_local_pos(self, index: int) -> QPointF:
        """
        回傳第 index 個 input pin 在 Gate 本地座標系統中的位置
        （不含 scene 變換）
        """
        # 左邊邊界附近
        x = 0
        # 平均分配在高度上
        step = self.h / (self.input_count + 1)
        y = step * (index + 1)
        return QPointF(x, y)

    def get_output_pin_local_pos(self) -> QPointF:
        """回傳 output pin 在 Gate 本地座標中的位置（右邊中間）"""
        x = self.w
        y = self.h / 2
        return QPointF(x, y)

    def get_input_pin_scene_pos(self, index: int) -> QPointF:
        """轉成 scene 座標"""
        return self.mapToScene(self.get_input_pin_local_pos(index))

    def get_output_pin_scene_pos(self) -> QPointF:
        return self.mapToScene(self.get_output_pin_local_pos())

    def hit_test_pin(self, scene_pos: QPointF, radius: float = 8.0):
        """
        檢查滑鼠點擊位置是否在某個 pin 上。
        若 hit 到，回傳 (kind, index)：
          kind = "in" / "out"
          index = 第幾個 input（out 的時候給 0）
        若沒 hit，回傳 None
        """
        # 先測 input pins
        for i in range(self.input_count):
            p = self.get_input_pin_scene_pos(i)
            if (p - scene_pos).manhattanLength() <= radius:
                return ("in", i)

        # 再測 output pin
        p_out = self.get_output_pin_scene_pos()
        if (p_out - scene_pos).manhattanLength() <= radius:
            return ("out", 0)

        return None

    # --- 畫圖：把 pin 畫出來 ---

    def paint(self, painter: QPainter, option, widget=None):
        # 先畫原本的矩形
        super().paint(painter, option, widget)

        # 畫 input pins（小圓點）
        painter.setBrush(Qt.black)
        r = 4  # 半徑
        for i in range(self.input_count):
            lp = self.get_input_pin_local_pos(i)
            painter.drawEllipse(int(lp.x() - r), int(lp.y() - r), 2 * r, 2 * r)

        # 畫 output pin
        lp_out = self.get_output_pin_local_pos()
        painter.drawEllipse(int(lp_out.x() - r), int(lp_out.y() - r), 2 * r, 2 * r)

    # --- 讓 wire 知道 gate 有移動 ---

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            # Gate 被拖動時，更新所有接上的 wires
            for w in self.connected_wires:
                w.update_path()
        return super().itemChange(change, value)

    # --- for WireItem 使用 ---

    def add_wire(self, wire):
        if wire not in self.connected_wires:
            self.connected_wires.append(wire)

    def remove_wire(self, wire):
        if wire in self.connected_wires:
            self.connected_wires.remove(wire)


# ===============================
# WireItem：連接兩個 gate 的線
# ===============================

class WireItem(QGraphicsPathItem):
    """
    代表一條線，連接 start_gate 的某個 pin 到 end_gate 的某個 pin。
    """

    def __init__(self, start_gate: GateItem, start_kind: str, start_index: int):
        super().__init__()
        self.start_gate = start_gate
        self.start_kind = start_kind  # "in" / "out"，實際上通常會是 "out"
        self.start_index = start_index

        self.end_gate = None
        self.end_kind = None
        self.end_index = None

        self.temp_end_pos = None  # 拉線過程中的暫時終點

        self.setPen(QPen(Qt.black, 2))
        self.setZValue(-1)  # 讓線在 gate 下面一點
        self.setFlags(
            QGraphicsItem.ItemIsSelectable |
            QGraphicsItem.ItemSendsGeometryChanges
        )

        # 一開始先記錄在起點 gate
        self.start_gate.add_wire(self)

        self.update_path()

    def set_temp_end_pos(self, pos: QPointF):
        """拉線過程中，滑鼠的位置作為暫時終點"""
        self.temp_end_pos = pos
        self.update_path()

    def finalize_connection(self, end_gate: GateItem, end_kind: str, end_index: int):
        """完成連線，指定終點 gate / pin"""
        self.end_gate = end_gate
        self.end_kind = end_kind
        self.end_index = end_index
        self.temp_end_pos = None
        self.end_gate.add_wire(self)
        self.update_path()

    def update_path(self):
        """依據目前 start / end gate 位置更新路徑"""
        if self.start_kind == "out":
            start = self.start_gate.get_output_pin_scene_pos()
        else:
            start = self.start_gate.get_input_pin_scene_pos(self.start_index)

        if self.end_gate is not None:
            # 已連到另一個 gate
            if self.end_kind == "in":
                end = self.end_gate.get_input_pin_scene_pos(self.end_index)
            else:
                end = self.end_gate.get_output_pin_scene_pos()
        else:
            # 尚未確定終點，用暫時滑鼠位置
            end = self.temp_end_pos if self.temp_end_pos is not None else start

        # 這裡先畫成直線；之後可以改成折線 / 貝氏曲線
        path = QPainterPath()
        path.moveTo(start)
        path.lineTo(end)
        self.setPath(path)

    def has_gate(self, gate: GateItem) -> bool:
        return (gate is self.start_gate) or (gate is self.end_gate)

    def disconnect(self):
        """從 gate 的 connected_wires 中移除自己"""
        if self.start_gate:
            self.start_gate.remove_wire(self)
        if self.end_gate:
            self.end_gate.remove_wire(self)


# ===============================
# View：處理拉線 & Delete
# ===============================

class LogicEditorView(QGraphicsView):
    """
    顯示邏輯電路的 View：
      - 可以拖 gate
      - 點 pin → 拉線 → 再點另一個 pin
      - 選取 gate / 線 按 Delete 刪除
    """

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)

        # 拉線狀態
        self.drawing_wire = False
        self.current_wire = None  # type: WireItem | None

    # --- 滑鼠事件 ---

    def mousePressEvent(self, event):
        scene_pos = self.mapToScene(event.pos())

        if event.button() == Qt.LeftButton:
            # 先檢查是否點在某個 gate 的 pin 上
            hit_gate = None
            hit_kind = None
            hit_index = None

            for item in self.scene().items(scene_pos):
                if isinstance(item, GateItem):
                    res = item.hit_test_pin(scene_pos)
                    if res is not None:
                        hit_kind, hit_index = res
                        hit_gate = item
                        break

            if hit_gate is not None:
                # 有點到 pin
                if not self.drawing_wire:
                    # 目前沒有在拉線 → 以這個 pin 當起點
                    # 通常從 output 開始拉，比較直覺，但這裡 in/out 都允許
                    self.drawing_wire = True
                    self.current_wire = WireItem(
                        start_gate=hit_gate,
                        start_kind=hit_kind,
                        start_index=hit_index
                    )
                    self.scene().addItem(self.current_wire)
                    self.current_wire.set_temp_end_pos(scene_pos)
                    # 不讓 QGraphicsView 再處理這次點擊（避免選取狀態亂掉）
                    return
                else:
                    # 已經在拉線狀態 → 這次點擊用來當終點
                    if self.current_wire is not None:
                        # 不能自己接自己同一個 pin（這裡只做簡單判斷）
                        self.current_wire.finalize_connection(
                            end_gate=hit_gate,
                            end_kind=hit_kind,
                            end_index=hit_index
                        )
                    self.drawing_wire = False
                    self.current_wire = None
                    return
            else:
                # 點在空白處，如果正在拉線就取消
                if self.drawing_wire and self.current_wire is not None:
                    self.current_wire.disconnect()
                    self.scene().removeItem(self.current_wire)
                    self.current_wire = None
                    self.drawing_wire = False
                    # 不 return，讓下面的 super 可以處理選取 / 框選等
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drawing_wire and self.current_wire is not None:
            scene_pos = self.mapToScene(event.pos())
            self.current_wire.set_temp_end_pos(scene_pos)
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event):
        # Delete 鍵：刪除選取的 gate 或 wire
        if event.key() == Qt.Key_Delete:
            for item in list(self.scene().selectedItems()):
                if isinstance(item, WireItem):
                    item.disconnect()
                    self.scene().removeItem(item)
                elif isinstance(item, GateItem):
                    # 先把接到這個 gate 的 wires 一併移除
                    for w in list(item.connected_wires):
                        w.disconnect()
                        self.scene().removeItem(w)
                    self.scene().removeItem(item)
            return  # 不再交給父類別處理
        super().keyPressEvent(event)


# ===============================
# MainWindow：工具列 + Scene
# ===============================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Python Logic Gate Editor (Mini Quartus Style)")
        self.resize(800, 600)

        # 建立場景 & 視圖
        self.scene = QGraphicsScene(self)
        self.scene.setSceneRect(0, 0, 2000, 2000)
        self.view = LogicEditorView(self.scene, self)
        self.setCentralWidget(self.view)

        # 工具列：新增 AND / OR / NOT gate
        toolbar = QToolBar("Gate Toolbar", self)
        self.addToolBar(toolbar)

        add_and_action = QAction("AND", self)
        add_and_action.triggered.connect(lambda: self.add_gate("AND"))
        toolbar.addAction(add_and_action)

        add_or_action = QAction("OR", self)
        add_or_action.triggered.connect(lambda: self.add_gate("OR"))
        toolbar.addAction(add_or_action)

        add_not_action = QAction("NOT", self)
        add_not_action.triggered.connect(lambda: self.add_gate("NOT"))
        toolbar.addAction(add_not_action)

        # 一開始先放幾個 gate
        self.add_gate("AND", x=100, y=100)
        self.add_gate("OR", x=300, y=150)
        self.add_gate("NOT", x=500, y=200)

    def add_gate(self, gate_type, x=50, y=50):
        gate = GateItem(gate_type=gate_type, x=x, y=y)
        self.scene.addItem(gate)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
