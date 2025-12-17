import sys
from typing import List, Dict, Optional

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QGraphicsView, QGraphicsScene,
    QGraphicsRectItem, QGraphicsPathItem, QGraphicsSimpleTextItem,
    QGraphicsItem,
    QGraphicsEllipseItem,
    QToolBar, QAction,
    QVBoxLayout, QHBoxLayout,
    QLabel, QSpinBox, QPushButton, QLineEdit, QMessageBox,QPlainTextEdit, QCheckBox
)
from PyQt5.QtGui import QBrush, QPen, QPainter, QPainterPath,QFont
from PyQt5.QtCore import Qt, QPointF, QEvent, QTimer

# 簡單的模組層級剪貼簿（儲存最近一次 copy/cut 的 gate 資訊）
GATE_CLIPBOARD = None
# 拉線貼齊半徑（像素）
SNAP_RADIUS = 14
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


# ============================================================
#                      GateItem  (多輸出版)
# ============================================================

class GateItem(QGraphicsRectItem):
    """
    Gate 類型：
      - IN  : 0 input, 1 output
      - OUT : 1 input, 0 output（value 代表看到的輸出值）
      - NOT : 1 input, 1 output
      - AND/OR : 2 input, 1 output
      - 其他字串（XOR, XNOR, FA...）預設也是 2 in / 1 out，
        但可以在建立時用 input_count / output_count 覆寫。
    """
    def __init__(
        self,
        gate_type: str = "AND",
        x: float = 0,
        y: float = 0,
        w: float = 120,
        h: float = 50,
        input_count: Optional[int] = None,
        output_count: Optional[int] = None,
    ):
        super().__init__(0, 0, w, h)
        self.gate_type = gate_type
        # 預設腳數
        if gate_type == "IN":
            default_in, default_out = 0, 1
        elif gate_type == "OUT":
            default_in, default_out = 1, 0
        elif gate_type == "NOT":
            default_in, default_out = 1, 1
        elif gate_type in ("DFF", "TFF"):
            # 簡單設計：D/T & CLK 兩個輸入，一個輸出 Q
            default_in, default_out = 2, 1
        else:
            default_in, default_out = 2, 1

        self.input_count = default_in if input_count is None else input_count
        self.output_count = default_out if output_count is None else output_count
        self.has_output = self.output_count > 0

        # 根據腳數調整高度
        self.w = w
        max_pins = max(self.input_count, self.output_count, 1)
        self.h = max(60, 40 * max_pins)
        self.setRect(0, 0, self.w, self.h)

        # 連線資訊
        self.connected_wires: List["WireItem"] = []
        self.input_wires: List[Optional["WireItem"]] = [None] * self.input_count
        # 每個輸出 pin 對應一個 list，可接多條線
        self.output_wires: List[List["WireItem"]] = [[] for _ in range(self.output_count)]

        # 邏輯值
        #  - IN / OUT：用 value 表示目前狀態（用來顯示顏色）
        #  - 其他 gate：通常看 out_values[0]
        self.value: bool = False
        self.out_values: List[bool] = [False] * self.output_count
        self.ff_state: bool = False


        # custom gate 定義（如果是 custom gate 的 instance）
        self.custom_def = None

        # 顯示名稱（IN1, OUT2...）
        self.param_name: Optional[str] = None

        # 外觀
        self.setBrush(QBrush(Qt.white))
        self.setPen(QPen(Qt.black, 2))
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )

        # 鎖定狀態（若 locked=True 則不可移動）
        self.locked = False

        # label: use a larger font for improved readability
        self.label = QGraphicsSimpleTextItem(self.gate_label_text(), self)
        font = QFont()
        font.setPointSize(9)      # 提升字體大小
        font.setBold(True)
        self.label.setFont(font)
        b = self.label.boundingRect()
        self.label.setPos((self.w - b.width()) / 2, (self.h - b.height()) / 2)

        self.setPos(x, y)

    # ---------- label 文字 ----------
    def gate_label_text(self) -> str:
        if self.param_name and self.gate_type in ("IN", "OUT"):
            return self.param_name
        return self.gate_type

    # ---------- pin 位置 ----------
    def get_input_pin_local_pos(self, i: int) -> QPointF:
        x = 0
        step = self.h / (self.input_count + 1)
        return QPointF(x, step * (i + 1))

    def get_output_pin_local_pos(self, j: int = 0) -> QPointF:
        x = self.w
        step = self.h / (self.output_count + 1)
        return QPointF(x, step * (j + 1))

    def get_input_pin_scene_pos(self, i: int) -> QPointF:
        return self.mapToScene(self.get_input_pin_local_pos(i))

    def get_output_pin_scene_pos(self, j: int = 0) -> QPointF:
        return self.mapToScene(self.get_output_pin_local_pos(j))

    def hit_test_pin(self, scene_pos: QPointF, r: float = 20.0):
        # input pins
        for i in range(self.input_count):
            p = self.get_input_pin_scene_pos(i)
            if (p - scene_pos).manhattanLength() <= r:
                return ("in", i)
        # output pins
        for j in range(self.output_count):
            p = self.get_output_pin_scene_pos(j)
            if (p - scene_pos).manhattanLength() <= r:
                return ("out", j)
        return None

    # ---------- 繪製 ----------
    def paint(self, painter: QPainter, option, widget=None):
        """
        改成使用馬卡龍（馬卡龍色系）漸層與圓角外觀。
        並在右下角顯示數字 0 / 1（表示該 gate 的「值」），取代先前用顏色深淺表示。
        """
        # 延遲 import 以免 top-level 變動太多
        from PyQt5.QtGui import QLinearGradient, QColor, QPen, QFontMetricsF

        rect = self.rect()

        # 馬卡龍色系 palette（可換成你提供的 hex）
        palette = {
            "IN": "#C8F7E1",
            "OUT": "#FFF5BA",
            "AND": "#D7EEFF",
            "OR": "#FFDAB9",
            "NOT": "#E8D5FF",
            "DFF": "#FFD1DC",
            "TFF": "#FFD1DC",
            "CUSTOM": "#FBE8FF",
        }

        if self.gate_type in palette:
            base_hex = palette[self.gate_type]
        elif self.custom_def is not None:
            base_hex = palette["CUSTOM"]
        else:
            base_hex = "#F3F4F6"  # fallback淡灰

        base_col = QColor(base_hex)

        # 改動：不再以 value 改變整體顏色深淺（使用固定馬卡龍漸層）
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, base_col.lighter(110))
        grad.setColorAt(1.0, base_col.darker(105))

        pen_color = QColor(90, 90, 90)
        painter.setPen(QPen(pen_color, 1.6))
        painter.setBrush(grad)

        radius = 8.0
        painter.drawRoundedRect(rect, radius, radius)

        # 選取時用較明顯的邊框
        if self.isSelected():
            painter.setPen(QPen(QColor(80, 140, 140), 2.8))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius, radius)

        # subtle inner stroke
        painter.setPen(QPen(QColor(255, 255, 255, 60), 0.8))
        inner = rect.adjusted(2, 2, -2, -2)
        painter.drawRoundedRect(inner, max(0, radius - 2), max(0, radius - 2))

        # draw pins as circles with two-tone (外圈白、內圈深)
        painter.setPen(QPen(QColor(70, 70, 70), 1.0))
        pin_r = 8
        for i in range(self.input_count):
            p = self.get_input_pin_local_pos(i)
            # outer
            painter.setBrush(QColor(255, 255, 255))
            painter.drawEllipse(p, pin_r, pin_r)
            # inner
            painter.setBrush(QColor(80, 80, 80))
            painter.drawEllipse(p, pin_r - 2, pin_r - 2)

        for j in range(self.output_count):
            p = self.get_output_pin_local_pos(j)
            painter.setBrush(QColor(255, 255, 255))
            painter.drawEllipse(p, pin_r, pin_r)
            painter.setBrush(QColor(80, 80, 80))
            painter.drawEllipse(p, pin_r - 2, pin_r - 2)

        # label: 確保使用較大的字體並置中
        try:
            f = QFont()
            f.setPointSize(9)
            f.setBold(True)
            self.label.setFont(f)
            b = self.label.boundingRect()
            self.label.setPos((self.w - b.width()) / 2, (self.h - b.height()) / 2)
        except Exception:
            pass

        # ------------------------------
        # 在右下角畫出 0 / 1（代表 gate 的值）
        # 對於 IN/OUT：使用 self.value；其他 gate：使用 out_values[0]（若有）
        # ------------------------------
        if self.gate_type in ("IN", "OUT"):
            v = self.value
        else:
            v = self.out_values[0] if self.output_count > 0 else False

        digit = "1" if v else "0"
        # small font for the digit
        font_small = QFont()
        font_small.setPointSize(8)
        font_small.setBold(True)
        painter.setFont(font_small)
        fm = painter.fontMetrics()
        w_text = fm.horizontalAdvance(digit)
        h_text = fm.height()
        margin_x = 6
        margin_y = 4
        x = rect.right() - margin_x - w_text
        y = rect.bottom() - margin_y
        painter.setPen(QPen(QColor(60, 60, 60), 1.0))
        painter.drawText(int(x), int(y), digit)

    # ---------- 移動時更新 wire ----------
    def itemChange(self, change, value):
        GRID_SIZE= 20
        if change == QGraphicsItem.ItemPositionChange and not self.locked:
            # value 是「即將要移動到的位置」
            x = round(value.x() / GRID_SIZE) * GRID_SIZE
            y = round(value.y() / GRID_SIZE) * GRID_SIZE
            return QPointF(x, y)

        if change == QGraphicsItem.ItemPositionHasChanged:
            for w in self.connected_wires:
                w.update_path()

            sc = self.scene()
            if sc is not None:
                views = sc.views()
                if views:
                    views[0].viewport().update()


        return super().itemChange(change, value)


    # ---------- wire 管理 ----------
    def add_wire(self, wire: "WireItem"):
        if wire not in self.connected_wires:
            self.connected_wires.append(wire)

    def remove_wire(self, wire: "WireItem"):
        if wire in self.connected_wires:
            self.connected_wires.remove(wire)
        for i, w in enumerate(self.input_wires):
            if w is wire:
                self.input_wires[i] = None
        for lst in self.output_wires:
            if wire in lst:
                lst.remove(wire)

    def connect_input(self, i: int, wire: "WireItem"):
        if 0 <= i < self.input_count:
            if self.input_wires[i] and self.input_wires[i] is not wire:
                self.input_wires[i].disconnect()
            self.input_wires[i] = wire
            self.add_wire(wire)

    def connect_output(self, j: int, wire: "WireItem"):
        if 0 <= j < self.output_count:
            if wire not in self.output_wires[j]:
                self.output_wires[j].append(wire)
            self.add_wire(wire)

    # ---------- IN gate 雙擊切換 ----------
    def mouseDoubleClickEvent(self, event):
        if self.gate_type == "IN":
            self.value = not self.value
            for k in range(self.output_count):
                self.out_values[k] = self.value

            self.update_display()
            self.update()  # ★ 立刻重繪 GateItem

            # ★ 立刻跑一次 simulate（讓 OUT 也跟著更新）
            sc = self.scene()
            if sc is not None:
                views = sc.views()
                if views:
                    w = views[0].window()
                    if hasattr(w, "simulate_main_circuit"):
                        w.simulate_main_circuit()

            event.accept()
            return

        super().mouseDoubleClickEvent(event)


    # ---------- 右鍵選單（刪除） ----------
    def contextMenuEvent(self, event):
        from PyQt5.QtWidgets import QMenu
        from PyQt5.QtGui import QCursor
        global GATE_CLIPBOARD

        menu = QMenu()
        act_copy = menu.addAction("Copy")
        act_cut = menu.addAction("Cut")
        # Lock/Unlock
        act_lock = menu.addAction("Lock Position" if not self.locked else "Unlock Position")
        menu.addSeparator()
        act_delete = menu.addAction("Delete")
        # 使用全域游標位置顯示選單，並延遲實際刪除動作，避免在事件處理中立刻移除 item
        chosen = menu.exec_(QCursor.pos())
        if chosen == act_copy:
            # 儲存到模組層級剪貼簿
            GATE_CLIPBOARD = {
                "gate_type": self.gate_type,
                "input_count": self.input_count,
                "output_count": self.output_count,
                "param_name": self.param_name,
                "value": self.value,
            }
            return
        elif chosen == act_cut:
            GATE_CLIPBOARD = {
                "gate_type": self.gate_type,
                "input_count": self.input_count,
                "output_count": self.output_count,
                "param_name": self.param_name,
                "value": self.value,
            }
            def do_cut():
                scene = self.scene()
                for w in list(self.connected_wires):
                    try:
                        w.disconnect()
                    except Exception:
                        pass
                    if w.scene() is not None:
                        w.scene().removeItem(w)
                if scene is not None:
                    try:
                        scene.removeItem(self)
                    except Exception:
                        pass
            QTimer.singleShot(0, do_cut)
            return
        elif chosen == act_lock:
            # toggle lock
            self.locked = not self.locked
            # 設定移動 flag
            self.setFlag(QGraphicsItem.ItemIsMovable, not self.locked)
            # 視覺提示
            self.setOpacity(0.7 if self.locked else 1.0)
            return
        elif chosen == act_delete:
            def do_delete():
                scene = self.scene()
                # 先斷開並移除所有連線
                for w in list(self.connected_wires):
                    try:
                        w.disconnect()
                    except Exception:
                        pass
                    if w.scene() is not None:
                        w.scene().removeItem(w)

                # 再從 scene 移除自己
                if scene is not None:
                    try:
                        scene.removeItem(self)
                    except Exception:
                        pass

            QTimer.singleShot(0, do_delete)

    # ---------- 顯示更新 ----------
    def update_display(self):
        # 改動：不再以 brush 顏色表示值（value）；顯示交由 paint 處理右下角數字
        self.label.setText(self.gate_label_text())
        b = self.label.boundingRect()
        self.label.setPos((self.w - b.width()) / 2, (self.h - b.height()) / 2)

class JunctionItem(GateItem):
    """線上的交會節點：1 input -> 1 output (buffer)，畫成小圓點"""
    def __init__(self, x: float, y: float, r: float = 6.0):
        # 用很小的矩形當作外框（GateItem 需要 rect）
        super().__init__(gate_type="JUNC", x=x, y=y, w=2*r+2, h=2*r+2, input_count=1, output_count=1)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.label.setText("")              # 不顯示字
        self.setPen(QPen(Qt.transparent, 0))
        self.setBrush(QBrush(Qt.transparent))
        self.r = r

    def itemChange(self, change, value):
            # Junction 不做 20px grid snap，否則新建時會被吸走造成分岔點偏移
            if change == QGraphicsItem.ItemPositionChange:
                return value

            if change == QGraphicsItem.ItemPositionHasChanged:
                for w in self.connected_wires:
                    w.update_path()
                sc = self.scene()
                if sc is not None:
                    views = sc.views()
                    if views:
                        views[0].viewport().update()

            return super(GateItem, self).itemChange(change, value)
    def get_input_pin_local_pos(self, i: int) -> QPointF:
        # 左側
        return QPointF(0, self.rect().height()/2)

    def get_output_pin_local_pos(self, j: int = 0) -> QPointF:
        # 右側
        return QPointF(self.rect().width(), self.rect().height()/2)

    def paint(self, painter: QPainter, option, widget=None):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        c = QPointF(self.rect().width()/2, self.rect().height()/2)
        painter.setPen(QPen(Qt.black, 1))
        painter.setBrush(QBrush(Qt.black))
        painter.drawEllipse(c, self.r, self.r)
        painter.restore()

    def update_display(self):
        # Junction 不需要 label
        pass

# ============================================================
#                        WireItem
# ============================================================

class WireItem(QGraphicsPathItem):
    """
    連線：
      start_gate (start_kind, start_index) -> end_gate (end_kind, end_index)
      start_kind / end_kind: "in" 或 "out"
      start_index / end_index: pin index
    """

    def __init__(self, start_gate: GateItem, start_kind: str, start_index: int):
        super().__init__()
        self.start_gate = start_gate
        self.start_kind = start_kind
        self.start_index = start_index

        self.end_gate: Optional[GateItem] = None
        self.end_kind: Optional[str] = None
        self.end_index: Optional[int] = None

        self.temp_end_pos: Optional[QPointF] = None

        self.setPen(QPen(Qt.black, 2))
        self.setZValue(-1)
        self.setFlags(
            QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )

        self.start_gate.add_wire(self)
        # wire name & label
        self.name: str = ""
        self.label_item: Optional[QGraphicsSimpleTextItem] = None
        self.update_path()

    def set_temp_end_pos(self, pos: QPointF):
        self.temp_end_pos = pos
        self.update_path()

    def finalize_connection(self, gate: GateItem, kind: str, index: int,ask_name: bool = False):
        self.end_gate = gate
        self.end_kind = kind
        self.end_index = index
        self.temp_end_pos = None

        # 兩端 gate 接上對應 pin
        if self.start_kind == "out":
            self.start_gate.connect_output(self.start_index, self)
        else:
            self.start_gate.connect_input(self.start_index, self)

        if self.end_kind == "in":
            self.end_gate.connect_input(self.end_index, self)
        else:
            self.end_gate.connect_output(self.end_index, self)

        # Ask for a name for this wire (optional)
        if ask_name:
            try:
                from PyQt5.QtWidgets import QInputDialog
                parent_widget = None
                try:
                    views = self.start_gate.scene().views()
                    parent_widget = views[0] if views else None
                except Exception:
                    parent_widget = None
                name, ok = QInputDialog.getText(parent_widget, "Wire Name", "Enter wire name (optional):")
                if ok and name:
                    self.name = name
                    # create label item in scene
                    if self.label_item is None and self.start_gate.scene() is not None:
                        self.label_item = QGraphicsSimpleTextItem(self.name)
                        self.label_item.setZValue(0)
                        self.start_gate.scene().addItem(self.label_item)
                    elif self.label_item is not None:
                        self.label_item.setText(self.name)
            except Exception:
                pass

        self.update_path()

    def update_path(self):
        # 起點
        if self.start_gate is not None:
            if self.start_kind == "out":
                s = self.start_gate.get_output_pin_scene_pos(self.start_index)
            else:
                s = self.start_gate.get_input_pin_scene_pos(self.start_index)
        else:
            s = QPointF(0, 0)

        # 終點
        if self.end_gate is not None:
            if self.end_kind == "in":
                e = self.end_gate.get_input_pin_scene_pos(self.end_index)
            else:
                e = self.end_gate.get_output_pin_scene_pos(self.end_index)
        else:
            e = self.temp_end_pos if self.temp_end_pos is not None else s
        OFFSET=30
        if getattr(self.start_gate, "gate_type", "") == "JUNC":
            OFFSET = 0

        path = QPainterPath()
        path.moveTo(s)

        # 直角路徑：先水平再垂直（你也可以換成先垂直再水平）
        if self.start_kind == "out":
            p1 = QPointF(s.x() + OFFSET, s.y())
        else:
            p1 = QPointF(s.x() - OFFSET, s.y())

        # 再走到跟終點同一個 y 或 x（這裡用水平->垂直->水平）
        p2 = QPointF(p1.x(), e.y())
        path.lineTo(p1)
        path.lineTo(p2)
        path.lineTo(e)
        self.setPath(path)
        # label 放在「整條線的中間」（用 s 與 e 的中點即可）
        if self.label_item is not None:
            mx = (s.x() + e.x()) / 2.0
            my = (s.y() + e.y()) / 2.0
            br = self.label_item.boundingRect()
            self.label_item.setPos(mx - br.width() / 2, my - br.height() / 2 - 6)

    def disconnect(self):
        if self.start_gate:
            self.start_gate.remove_wire(self)
        if self.end_gate:
            self.end_gate.remove_wire(self)
        # remove label if any
        if self.label_item is not None:
            try:
                if self.label_item.scene() is not None:
                    self.label_item.scene().removeItem(self.label_item)
            except Exception:
                pass
            self.label_item = None

    def mouseDoubleClickEvent(self, event):
        """雙擊線時彈出對話框讓使用者修改名稱"""
        try:
            from PyQt5.QtWidgets import QInputDialog
            parent_widget = None
            try:
                sc = self.start_gate.scene() if self.start_gate is not None else None
                views = sc.views() if sc is not None else []
                parent_widget = views[0] if views else None
            except Exception:
                parent_widget = None

            name, ok = QInputDialog.getText(parent_widget, "Rename Wire", "Enter wire name:", text=self.name)
            if ok:
                self.name = name
                if self.label_item is None and self.start_gate is not None and self.start_gate.scene() is not None:
                    self.label_item = QGraphicsSimpleTextItem(self.name)
                    self.label_item.setZValue(0)
                    self.start_gate.scene().addItem(self.label_item)
                elif self.label_item is not None:
                    self.label_item.setText(self.name)
                self.update_path()
        except Exception:
            pass
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        from PyQt5.QtWidgets import QMenu
        from PyQt5.QtGui import QCursor

        menu = QMenu()
        act_add_junc = menu.addAction("Add Junction Here")
        act_rename = menu.addAction("Rename")
        menu.addSeparator()
        act_delete = menu.addAction("Delete")

        chosen = menu.exec_(QCursor.pos())

        if chosen == act_add_junc:
            try:
                scene_pos = event.scenePos()
                sc = self.scene()
                if sc is not None:
                    views = sc.views()
                    if views:
                        view = views[0]
                        if hasattr(view, "_split_wire_with_junction"):
                            view._split_wire_with_junction(self, scene_pos)
                            win = view.window()
                            if hasattr(win, "simulate_main_circuit"):
                                win.simulate_main_circuit()
            except Exception:
                pass
            return

        if chosen == act_rename:
            self.mouseDoubleClickEvent(event)
            return

        if chosen == act_delete:
            try:
                self.disconnect()
            except Exception:
                pass
            try:
                if self.scene() is not None:
                    self.scene().removeItem(self)
            except Exception:
                pass
            return




# ============================================================
#                        CircuitView
# ============================================================
from PyQt5.QtCore import QLineF 
class CircuitView(QGraphicsView):
    def __init__(self, scene: QGraphicsScene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)

        self.drawing_wire = False
        self.current_wire: Optional[WireItem] = None
        # hover indicator for overlapping wires
        self.hovered_wire: Optional[WireItem] = None
        self.hover_dots: List[QGraphicsEllipseItem] = []
        # snap indicator and target
        self.snap_indicator: Optional[QGraphicsEllipseItem] = None
        self.snap_target = None
        self.hover_point: Optional[QPointF] = None
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)


    def _find_or_create_junction(self, pos: QPointF, r: float = 8.0) -> JunctionItem:
        # 找半徑內既有 junction
        for it in self.scene().items():
            if isinstance(it, JunctionItem):
                c = it.sceneBoundingRect().center()
                if (c - pos).manhattanLength() <= r:
                    return it

        j = JunctionItem(pos.x() - 7, pos.y() - 7, r=5.0)
        self.scene().addItem(j)
        return j

    def _nearest_junction(self, pos: QPointF, r: float = 18.0) -> Optional[JunctionItem]:
        for it in self.scene().items():
            if isinstance(it, JunctionItem):
                c = it.sceneBoundingRect().center()
                if (c - pos).manhattanLength() <= r:
                    return it
        return None

    def _closest_point_on_segment(self, p: QPointF, a: QPointF, b: QPointF):
        ax, ay = a.x(), a.y()
        bx, by = b.x(), b.y()
        px, py = p.x(), p.y()

        dx = bx - ax
        dy = by - ay
        if dx == 0 and dy == 0:
            return a, (px - ax) ** 2 + (py - ay) ** 2

        t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
        t = max(0.0, min(1.0, t))
        cx = ax + t * dx
        cy = ay + t * dy
        dist2 = (px - cx) ** 2 + (py - cy) ** 2
        return QPointF(cx, cy), dist2

    def _closest_point_on_wire(self, wire: WireItem, p: QPointF):
        # wire 的折線節點跟 update_path 一致：s -> p1 -> p2 -> e
        if wire.start_gate is None or wire.end_gate is None:
            return None, 1e18

        if wire.start_kind == "out":
            s = wire.start_gate.get_output_pin_scene_pos(wire.start_index)
        else:
            s = wire.start_gate.get_input_pin_scene_pos(wire.start_index)

        if wire.end_kind == "in":
            e = wire.end_gate.get_input_pin_scene_pos(wire.end_index)
        else:
            e = wire.end_gate.get_output_pin_scene_pos(wire.end_index)

        OFFSET = 30
        if wire.start_kind == "out":
            p1 = QPointF(s.x() + OFFSET, s.y())
        else:
            p1 = QPointF(s.x() - OFFSET, s.y())
        p2 = QPointF(p1.x(), e.y())

        best_pt, best_d2 = None, 1e18
        for a, b in [(s, p1), (p1, p2), (p2, e)]:
            cpt, d2 = self._closest_point_on_segment(p, a, b)
            if d2 < best_d2:
                best_d2 = d2
                best_pt = cpt

        return best_pt, best_d2

    def _split_wire_with_junction(self, wire: WireItem, at_pos: QPointF) -> Optional[JunctionItem]:
        if wire is None or wire.start_gate is None or wire.end_gate is None:
            return None

        scene = self.scene()

        # (A) ★如果點的位置附近已經有 junction：直接回傳它，不要再切線
        exist = self._nearest_junction(at_pos, r=18.0)
        if exist is not None:
            return exist

        # (B) 建立新 junction（你要的「垂直段往右挪」也保留）
        start_gate, start_idx = wire.start_gate, wire.start_index
        end_gate, end_idx     = wire.end_gate, wire.end_index

        # 算折線關鍵點：s -> p1 -> p2 -> e（要跟 update_path 一致）
        if wire.start_kind == "out":
            s = start_gate.get_output_pin_scene_pos(start_idx)
        else:
            s = start_gate.get_input_pin_scene_pos(start_idx)

        if wire.end_kind == "in":
            e = end_gate.get_input_pin_scene_pos(end_idx)
        else:
            e = end_gate.get_output_pin_scene_pos(end_idx)

        OFFSET = 30
        if getattr(start_gate, "gate_type", "") == "JUNC":
            OFFSET = -5

        p1 = QPointF(s.x() + OFFSET, s.y()) if wire.start_kind == "out" else QPointF(s.x() - OFFSET, s.y())
        p2 = QPointF(p1.x(), e.y())

        # 垂直段附近往右挪
        JUNC_NUDGE_X = 14
        at_pos2 = at_pos
        if abs(at_pos.x() - p1.x()) < 6:
            at_pos2 = QPointF(at_pos.x() + JUNC_NUDGE_X, at_pos.y())

        junc = self._find_or_create_junction(at_pos2)

        # (C) 把原 wire 換成兩段：start -> junc -> end
        wire.disconnect()
        try:
            if wire.scene() is not None:
                scene.removeItem(wire)
        except Exception:
            pass

        w1 = WireItem(start_gate, "out", start_idx)
        scene.addItem(w1)
        w1.finalize_connection(junc, "in", 0, ask_name=False)

        w2 = WireItem(junc, "out", 0)
        scene.addItem(w2)
        w2.finalize_connection(end_gate, "in", end_idx, ask_name=False)

        return junc


    def mousePressEvent(self, event):
        scene_pos = self.mapToScene(event.pos())
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)

        # =========================================================
        # 0) 不在拉線時：若滑鼠在既有 wire 附近（hovered_wire/hover_point 有值）
        #    => 先把該 wire 切開插入 junction，並從 junction 的 output 開始拉線（分岔）
        # =========================================================
        if (not self.drawing_wire) and (self.hovered_wire is not None) and (self.hover_point is not None):
            j = self._split_wire_with_junction(self.hovered_wire, self.hover_point)
            if j is not None:
                # 清掉 hover dots（視覺）
                try:
                    for d in list(getattr(self, "hover_dots", [])):
                        if d.scene() is not None:
                            d.scene().removeItem(d)
                except Exception:
                    pass
                self.hover_dots = []

                # 開始從 junction 拉一條新線（分岔）
                self.drawing_wire = True
                self.current_wire = WireItem(start_gate=j, start_kind="out", start_index=0)
                self.scene().addItem(self.current_wire)
                self.current_wire.set_temp_end_pos(scene_pos)
                return

        # =========================================================
        # 1) 在拉線中：嘗試接到 input pin（優先）
        # =========================================================
        if self.drawing_wire and self.current_wire is not None:
            hit_gate = None
            hit_kind = None
            hit_index = None

            # (1) 先精準 hit-test pin
            for item in self.scene().items(scene_pos):
                if isinstance(item, GateItem):
                    res = item.hit_test_pin(scene_pos)
                    if res is not None:
                        hit_kind, hit_index = res
                        hit_gate = item
                        break

            # (2) 若沒精準點到 pin，嘗試「吸附半徑內最近 pin」
            if hit_gate is None:
                best_gate = None
                best_kind = None
                best_idx = None
                best_dist = 1e18

                for item in self.scene().items(scene_pos):
                    if isinstance(item, GateItem):
                        # 找最近的 input pin（你系統只允許終點接 input）
                        for i_pin in range(item.input_count):
                            p = item.get_input_pin_scene_pos(i_pin)
                            d = (p - scene_pos).manhattanLength()
                            if d < best_dist:
                                best_dist = d
                                best_gate = item
                                best_kind = "in"
                                best_idx = i_pin

                if best_gate is not None and best_dist <= SNAP_RADIUS:
                    hit_gate, hit_kind, hit_index = best_gate, best_kind, best_idx

            # (3) 若成功命中 input pin 且不是接回自己 => finalize
            if (
                hit_gate is not None
                and hit_kind == "in"
                and hit_gate is not self.current_wire.start_gate
            ):
                self.current_wire.finalize_connection(gate=hit_gate, kind=hit_kind, index=hit_index)
                sc = self.scene()
                if sc is not None:
                    views = sc.views()
                    if views:
                        win = views[0].window()
                        if hasattr(win, "simulate_main_circuit"):
                            win.simulate_main_circuit()
                # 清掉 snap indicator
                try:
                    if getattr(self, "snap_indicator", None) is not None and self.snap_indicator.scene() is not None:
                        self.snap_indicator.scene().removeItem(self.snap_indicator)
                except Exception:
                    pass
                self.snap_indicator = None
                self.snap_target = None

                # 結束拉線
                self.drawing_wire = False
                self.current_wire = None

                # 清 hover dots
                try:
                    for d in list(getattr(self, "hover_dots", [])):
                        if d.scene() is not None:
                            d.scene().removeItem(d)
                except Exception:
                    pass
                self.hover_dots = []
                return

            # (4) 若沒命中 input pin，但 mouseMove 已有 snap_target（pin 吸附）
            if getattr(self, "snap_target", None) is not None:
                tgt_gate, tgt_kind, tgt_idx = self.snap_target
                if tgt_kind == "in" and tgt_gate is not self.current_wire.start_gate:
                    self.current_wire.finalize_connection(gate=tgt_gate, kind="in", index=tgt_idx)
                    sc = self.scene()
                    if sc is not None:
                        views = sc.views()
                        if views:
                            win = views[0].window()
                            if hasattr(win, "simulate_main_circuit"):
                                win.simulate_main_circuit()
                    # 清掉 snap indicator
                    try:
                        if getattr(self, "snap_indicator", None) is not None and self.snap_indicator.scene() is not None:
                            self.snap_indicator.scene().removeItem(self.snap_indicator)
                    except Exception:
                        pass
                    self.snap_indicator = None
                    self.snap_target = None

                    # 結束拉線
                    self.drawing_wire = False
                    self.current_wire = None

                    # 清 hover dots
                    try:
                        for d in list(getattr(self, "hover_dots", [])):
                            if d.scene() is not None:
                                d.scene().removeItem(d)
                    except Exception:
                        pass
                    self.hover_dots = []
                    return

            # (5) 其他狀況：視為取消這條線
            try:
                self.current_wire.disconnect()
            except Exception:
                pass
            try:
                if self.current_wire.scene() is not None:
                    self.scene().removeItem(self.current_wire)
            except Exception:
                pass

            self.current_wire = None
            self.drawing_wire = False

            try:
                for d in list(getattr(self, "hover_dots", [])):
                    if d.scene() is not None:
                        d.scene().removeItem(d)
            except Exception:
                pass
            self.hover_dots = []
            return

        # =========================================================
        # 2) 不在拉線中：嘗試從 output pin 開始拉線
        # =========================================================
        hit_gate = None
        hit_kind = None
        hit_index = None

        # (1) 先精準 hit-test pin
        for item in self.scene().items(scene_pos):
            if isinstance(item, GateItem):
                res = item.hit_test_pin(scene_pos)
                if res is not None:
                    hit_kind, hit_index = res
                    hit_gate = item
                    break

        # (2) 若沒精準點到 pin，回退：在 SNAP_RADIUS 內找最近 output pin
        if hit_gate is None:
            best_gate = None
            best_kind = None
            best_idx = None
            best_dist = 1e18

            for item in self.scene().items(scene_pos):
                if isinstance(item, GateItem):
                    # 只找 output pin
                    for j in range(item.output_count):
                        p = item.get_output_pin_scene_pos(j)
                        d = (p - scene_pos).manhattanLength()
                        if d < best_dist:
                            best_dist = d
                            best_gate = item
                            best_kind = "out"
                            best_idx = j

            if best_gate is not None and best_dist <= SNAP_RADIUS:
                hit_gate, hit_kind, hit_index = best_gate, best_kind, best_idx

        # (3) 若命中 output pin => 開始拉線
        if hit_gate is not None and hit_kind == "out":
            self.drawing_wire = True
            self.current_wire = WireItem(start_gate=hit_gate, start_kind="out", start_index=hit_index)
            self.scene().addItem(self.current_wire)
            self.current_wire.set_temp_end_pos(scene_pos)

            # 清 hover dots
            try:
                for d in list(getattr(self, "hover_dots", [])):
                    if d.scene() is not None:
                        d.scene().removeItem(d)
            except Exception:
                pass
            self.hover_dots = []
            return

        # =========================================================
        # 3) 其他：交回給 QGraphicsView 預設行為（框選、點選 gate 等）
        # =========================================================
        return super().mousePressEvent(event)
    def _dist2_to_path(self, path: QPainterPath, p: QPointF, samples: int = 60) -> float:
            best = 1e18
            for i in range(samples + 1):
                t = i / samples
                q = path.pointAtPercent(t)
                dx = q.x() - p.x()
                dy = q.y() - p.y()
                d2 = dx*dx + dy*dy
                if d2 < best:
                    best = d2
                    best_pt = q
            return best, best_pt
    def _dist_point_to_seg(self, p: QPointF, a: QPointF, b: QPointF):
        # 回傳 (dist2, closest_point)
        ax, ay = a.x(), a.y()
        bx, by = b.x(), b.y()
        px, py = p.x(), p.y()
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            cx, cy = ax, ay
        else:
            t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
            t = max(0.0, min(1.0, t))
            cx, cy = ax + t * dx, ay + t * dy
        dist2 = (px - cx) ** 2 + (py - cy) ** 2
        return dist2, QPointF(cx, cy)

    def _wire_hover_info(self, wire: "WireItem", scene_pos: QPointF):
        # 用 wire 的 QPainterPath（折線）逐段算距離
        path = wire.path()
        if path.elementCount() < 2:
            return None

        min_d2 = 1e18
        best_cp = None

        # path 是 wire 的 local coords，先把端點轉成 scene
        prev = wire.mapToScene(QPointF(path.elementAt(0).x, path.elementAt(0).y))
        for i in range(1, path.elementCount()):
            e = path.elementAt(i)
            cur = wire.mapToScene(QPointF(e.x, e.y))
            d2, cp = self._dist_point_to_seg(scene_pos, prev, cur)
            if d2 < min_d2:
                min_d2 = d2
                best_cp = cp
            prev = cur

        return min_d2, best_cp


    def _draw_hover_dots_on_wire(self, wire: "WireItem"):
        # 用 pointAtPercent 在 path 上取點（較順）
        path = wire.path()
        if path.elementCount() < 2:
            return

        # 先清舊點
        for d in list(getattr(self, "hover_dots", [])):
            try:
                if d.scene() is not None:
                    d.scene().removeItem(d)
            except Exception:
                pass
        self.hover_dots = []

        n = 5  # 會畫 4 個
        for i in range(1, n):
            tt = i / float(n)
            p_local = path.pointAtPercent(tt)
            p_scene = wire.mapToScene(p_local)
            dot = QGraphicsEllipseItem(p_scene.x() - 3, p_scene.y() - 3, 6, 6)
            dot.setBrush(QBrush(Qt.black))
            dot.setZValue(0.1)
            self.scene().addItem(dot)
            self.hover_dots.append(dot)
    def mouseMoveEvent(self, event):
        scene_pos = self.mapToScene(event.pos())

        # ===== 1) hover 線偵測（改成用 wire.path 的折線距離）=====
        hovered = None
        closest = None
        min_d2 = 1e18

        for item in self.scene().items():
            if isinstance(item, WireItem) and item.start_gate is not None and item.end_gate is not None:
                info = self._wire_hover_info(item, scene_pos)
                if info is None:
                    continue
                d2, cp = info
                if d2 < min_d2:
                    min_d2 = d2
                    hovered = item
                    closest = cp

        THRESH_SQ = 12 ** 2

        # 預設先清點
        for d in list(getattr(self, "hover_dots", [])):
            try:
                if d.scene() is not None:
                    d.scene().removeItem(d)
            except Exception:
                pass
        self.hover_dots = []

        if hovered is not None and min_d2 <= THRESH_SQ:
            self.hovered_wire = hovered
            self.hover_point = closest
            self._draw_hover_dots_on_wire(hovered)
        else:
            self.hovered_wire = None
            self.hover_point = None
        
        if self.drawing_wire and self.current_wire is not None:
            scene_pos = self.mapToScene(event.pos())
            # 首先嘗試 snap 到附近的 pin，如果找到則把臨時終點設為該 pin
            snap_found = False
            snap_pos = None
            snap_info = None
            for item in self.scene().items():
                if isinstance(item, GateItem) and item is not self.current_wire.start_gate:
                    res = item.hit_test_pin(scene_pos, r=SNAP_RADIUS)
                    if res is not None:
                        kind, idx = res
                        if kind == 'in':
                            p = item.get_input_pin_scene_pos(idx)
                        else:
                            p = item.get_output_pin_scene_pos(idx)
                        snap_found = True
                        snap_pos = p
                        snap_info = (item, kind, idx)
                        break

            if snap_found and snap_pos is not None:
                self.current_wire.set_temp_end_pos(snap_pos)
                self.snap_target = snap_info
                # show snap indicator
                try:
                    if hasattr(self, 'snap_indicator') and self.snap_indicator is not None:
                        if self.snap_indicator.scene() is None:
                            self.scene().addItem(self.snap_indicator)
                        self.snap_indicator.setRect(snap_pos.x()-6, snap_pos.y()-6, 12, 12)
                    else:
                        self.snap_indicator = QGraphicsEllipseItem(snap_pos.x()-6, snap_pos.y()-6, 12, 12)
                        self.snap_indicator.setBrush(QBrush(Qt.green))
                        self.snap_indicator.setOpacity(0.5)
                        self.snap_indicator.setZValue(2)
                        self.scene().addItem(self.snap_indicator)
                except Exception:
                    pass
            else:
                # 沒有 snap -> 使用滑鼠位置並移除 snap indicator
                self.current_wire.set_temp_end_pos(scene_pos)
                self.snap_target = None
                try:
                    if hasattr(self, 'snap_indicator') and self.snap_indicator is not None:
                        if self.snap_indicator.scene() is not None:
                            self.snap_indicator.scene().removeItem(self.snap_indicator)
                        self.snap_indicator = None
                except Exception:
                    pass
    
        super().mouseMoveEvent(event)

    def contextMenuEvent(self, event):
        from PyQt5.QtWidgets import QMenu
        global GATE_CLIPBOARD
        scene_pos = self.mapToScene(event.pos())

        # 如果目前有選取物件，優先顯示針對選取項目的選單（例如 Delete）
        selected = list(self.scene().selectedItems())
        if selected:
            menu = QMenu()
            act_delete = menu.addAction("Delete")
            chosen = menu.exec_(event.globalPos())
            if chosen == act_delete:
                # 與 keyPressEvent 的刪除行為一致
                for item in selected:
                    if isinstance(item, WireItem):
                        item.disconnect()
                        try:
                            if item.scene() is not None:
                                item.scene().removeItem(item)
                        except Exception:
                            pass
                    elif isinstance(item, GateItem):
                        for w in list(item.connected_wires):
                            try:
                                w.disconnect()
                            except Exception:
                                pass
                            try:
                                if w.scene() is not None:
                                    w.scene().removeItem(w)
                            except Exception:
                                pass
                        try:
                            if item.scene() is not None:
                                item.scene().removeItem(item)
                        except Exception:
                            pass
                # cleanup any hover/snap indicators
                try:
                    for d in list(getattr(self, 'hover_dots', [])):
                        if d.scene() is not None:
                            d.scene().removeItem(d)
                except Exception:
                    pass
                try:
                    if getattr(self, 'snap_indicator', None) is not None and self.snap_indicator.scene() is not None:
                        self.snap_indicator.scene().removeItem(self.snap_indicator)
                except Exception:
                    pass
                self.hover_dots = []
                self.snap_indicator = None
            return

        # 如果點到 gate，交給 gate 自己處理（讓 GateItem 處理其個別選單）
        items = self.scene().items(scene_pos)
        if any(isinstance(it, GateItem) for it in items):
            super().contextMenuEvent(event)
            return

        # 空白處選單（Paste）
        menu = QMenu()
        act_paste = None
        if GATE_CLIPBOARD is not None:
            act_paste = menu.addAction("Paste")

        chosen = menu.exec_(event.globalPos())
        if chosen == act_paste and GATE_CLIPBOARD is not None:
            data = GATE_CLIPBOARD.copy()
            g = GateItem(
                gate_type=data.get("gate_type", "AND"),
                x=scene_pos.x() - 20,
                y=scene_pos.y() - 10,
                input_count=data.get("input_count"),
                output_count=data.get("output_count"),
            )
            g.param_name = data.get("param_name")
            g.value = data.get("value", False)
            for k in range(g.output_count):
                g.out_values[k] = g.value
            g.update_display()
            self.scene().addItem(g)

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

    def drawBackground(self, painter: QPainter, rect):
            super().drawBackground(painter, rect)

            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, False)

            grid_size = 20

            left = int(rect.left()) - (int(rect.left()) % grid_size)
            top  = int(rect.top())  - (int(rect.top())  % grid_size)

            pen = QPen(Qt.lightGray)
            pen.setWidth(0)
            painter.setPen(pen)

            # 垂直線
            x = left
            while x < rect.right():
                painter.drawLine(QLineF(float(x), float(rect.top()),
                                        float(x), float(rect.bottom())))
                x += grid_size

            # 水平線
            y = top
            while y < rect.bottom():
                painter.drawLine(QLineF(float(rect.left()),  float(y),
                                        float(rect.right()), float(y)))
                y += grid_size

            painter.restore()

# ============================================================
#                Custom Gate Definition
# ============================================================

class CustomGateDefinition:
    def __init__(self, name: str, n_inputs: int, n_outputs: int, editor: "CircuitEditorWidget"):
        self.name = name
        self.n_inputs = n_inputs
        self.n_outputs = n_outputs
        self.editor = editor


# ============================================================
#                CircuitEditorWidget (子電路編輯器)
# ============================================================

class CircuitEditorWidget(QWidget):
    def __init__(self, parent=None, allow_io_edit=True):
        super().__init__(parent)

        self.scene = QGraphicsScene(self)
        self.scene.setSceneRect(0, 0, 3000, 2000)
        self.view = CircuitView(self.scene, self)

        self.toolbar = QToolBar("Subcircuit Toolbar", self)
        self.allow_io_edit = allow_io_edit

        self.in_count = 0
        self.out_count = 0

        if allow_io_edit:
            act_in = QAction("IN", self)
            act_in.triggered.connect(lambda: self.add_gate("IN"))
            self.toolbar.addAction(act_in)

            act_out = QAction("OUT", self)
            act_out.triggered.connect(lambda: self.add_gate("OUT"))
            self.toolbar.addAction(act_out)

        act_and = QAction("AND", self)
        act_and.triggered.connect(lambda: self.add_gate("AND"))
        self.toolbar.addAction(act_and)

        act_or = QAction("OR", self)
        act_or.triggered.connect(lambda: self.add_gate("OR"))
        self.toolbar.addAction(act_or)

        act_not = QAction("NOT", self)
        act_not.triggered.connect(lambda: self.add_gate("NOT"))
        self.toolbar.addAction(act_not)

        act_dff = QAction("DFF", self)
        act_dff.triggered.connect(lambda: self.add_gate("DFF"))
        self.toolbar.addAction(act_dff)

        act_tff = QAction("TFF", self)
        act_tff.triggered.connect(lambda: self.add_gate("TFF"))
        self.toolbar.addAction(act_tff)


        # custom gate 按鈕（由 MainWindow 填入）
        self.custom_gate_buttons: Dict[str, QAction] = {}

        layout = QVBoxLayout(self)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.view)

    # 新增 primitive gate
    def add_gate(self, gate_type: str, x: float = 80, y: float = 80) -> GateItem:
        g = GateItem(gate_type, x, y)
        if gate_type == "IN":
            self.in_count += 1
            g.param_name = f"IN{self.in_count}"
        elif gate_type == "OUT":
            self.out_count += 1
            g.param_name = f"OUT{self.out_count}"
        g.update_display()
        self.scene.addItem(g)
        return g

    # 新增 custom gate instance
    def add_custom_gate_instance(self, name: str, custom_defs: Dict[str, CustomGateDefinition]):
        defn = custom_defs[name]
        g = GateItem(
            gate_type=name,
            x=200,
            y=200,
            input_count=defn.n_inputs,
            output_count=defn.n_outputs,
        )
        g.custom_def = defn
        g.update_display()
        self.scene.addItem(g)
        return g

    # 更新工具列中的 custom gate 按鈕
    def refresh_custom_gate_buttons(self, custom_defs: Dict[str, CustomGateDefinition]):
        for name, act in self.custom_gate_buttons.items():
            self.toolbar.removeAction(act)
        self.custom_gate_buttons.clear()

        for name in custom_defs:
            act = QAction(name, self)
            act.triggered.connect(
                lambda checked=False, n=name: self.add_custom_gate_instance(n, custom_defs)
            )
            self.toolbar.addAction(act)
            self.custom_gate_buttons[name] = act

    def all_gates(self) -> List[GateItem]:
        return [item for item in self.scene.items() if isinstance(item, GateItem)]


# ============================================================
#             CustomGateEditorWindow（雙擊打開）
# ============================================================

class CustomGateEditorWindow(QMainWindow):
    def __init__(self, defn: CustomGateDefinition, main_window=None):
        super().__init__(main_window)
        self.defn = defn
        self.setWindowTitle(f"Custom Gate Editor: {defn.name}")
        self.editor = defn.editor
        self.setCentralWidget(self.editor)
        self.statusBar().showMessage(
            f"Editing custom gate '{defn.name}'    Inputs={defn.n_inputs}, Outputs={defn.n_outputs}"
        )


# ============================================================
#                  Main Window（核心系統）
# ============================================================
class WaveformWindow(QWidget):
    """
    用 matplotlib 畫 timing diagram 的小視窗。
    """
    def __init__(self, history, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Flow Waveforms")
        self.setWindowFlag(Qt.Window)
        self.history = history

        layout = QVBoxLayout(self)
        self.figure = Figure(figsize=(8, 6))
        self.canvas = FigureCanvas(self.figure)
        layout.addWidget(self.canvas)

        self.draw_waveforms()

    def draw_waveforms(self):
        self.figure.clear()
        ax = self.figure.add_subplot(111)

        if not self.history:
            ax.text(0.5, 0.5, "No data", ha="center", va="center")
            self.canvas.draw()
            return

        n_steps = len(self.history)

        # 收集所有 input / output 名稱
        input_names = set()
        output_names = set()
        for h in self.history:
            input_names.update(h["inputs"].keys())
            output_names.update(h["outputs"].keys())

        input_names = sorted(input_names)
        output_names = sorted(output_names)

        # 要畫的訊號順序：CLK -> inputs -> outputs
        signal_names = ["CLK"] + input_names + output_names

        # x 軸座標：step index（0,1,2,...）
        x_steps = list(range(n_steps + 1))  # 注意 +1，讓最後一段也畫出來

        for idx, name in enumerate(signal_names):
            base = 2 * idx  # 垂直位移

            if name == "CLK":
                # CLK：每一個 step 完整一個 0→1→0 週期，
                # rising edge 在整數格線上（0,1,2,...）
                t = []
                v = []

                current_t = 0.0
                for k in range(n_steps):
                    # 低電位到 step 起點
                    t.append(current_t)
                    v.append(0)

                    # rising edge：電平拉高（在同一個 x 再加一個點）
                    t.append(current_t)
                    v.append(1)

                    # 高電位維持到 step 中間
                    t.append(current_t + 0.5)
                    v.append(1)

                    # falling edge：掉回 0
                    t.append(current_t + 0.5)
                    v.append(0)

                    # 低電位維持到下一個 step 起點
                    t.append(current_t + 1.0)
                    v.append(0)

                    current_t += 1.0

                y = [base + vv * 0.8 for vv in v]
                ax.step(t, y, where="post")

            elif name in input_names:
                # inputs：每一拍一個值，只在 step 邊界（整數）跳變
                vals = [1 if h["inputs"].get(name, False) else 0 for h in self.history]
                # 最後再補一個，讓階梯畫到最後
                vals.append(vals[-1])
                y = [base + vv * 0.8 for vv in vals]
                ax.step(x_steps, y, where="post")

            else:
                # outputs：同樣只在 step 邊界跳變
                vals = [1 if h["outputs"].get(name, False) else 0 for h in self.history]
                vals.append(vals[-1])
                y = [base + vv * 0.8 for vv in vals]
                ax.step(x_steps, y, where="post")

            # 在左邊寫上訊號名稱
            ax.text(-0.3, base + 0.4, name, ha="right", va="center")

        ax.set_yticks([])
        ax.set_xticks(range(n_steps))
        ax.set_xlabel("Step (Flow line index)")
        ax.grid(True, axis="x", linestyle="--", alpha=0.3)

        self.figure.tight_layout()
        self.canvas.draw()



class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Python Logic Gate Editor (Custom & Nested)")
        self.resize(1400, 800)

        self.custom_defs: Dict[str, CustomGateDefinition] = {}
        self.custom_gate_actions: Dict[str, QAction] = {}

        central = QWidget(self)
        hbox = QHBoxLayout(central)
        hbox.setContentsMargins(0, 0, 0, 0)

        self.main_editor = CircuitEditorWidget(self, allow_io_edit=True)
        hbox.addWidget(self.main_editor, 3)

        # 右邊面板
        self.side_panel = QWidget(self)
        side_layout = QVBoxLayout(self.side_panel)

        side_layout.addWidget(QLabel("Inputs / Parameters"))

        # 動態列出 IN gate 的容器
        self.input_list_container = QWidget(self.side_panel)
        self.input_list_layout = QVBoxLayout(self.input_list_container)
        self.input_list_layout.setContentsMargins(0, 0, 0, 0)
        self.input_list_layout.setSpacing(4)
        side_layout.addWidget(self.input_list_container)
        # ---- Outputs ----
        side_layout.addWidget(QLabel("Outputs"))
        self.output_list_container = QWidget(self.side_panel)
        self.output_list_layout = QVBoxLayout(self.output_list_container)
        self.output_list_layout.setContentsMargins(0, 0, 0, 0)
        self.output_list_layout.setSpacing(4)
        side_layout.addWidget(self.output_list_container)

        side_layout.addStretch()
        side_layout.addStretch()

        # Flowchart 區域
        side_layout.addWidget(QLabel("Flowchart（每行一個狀態）"))
        self.flow_text = QPlainTextEdit(self.side_panel)
        self.flow_text.setPlaceholderText("例：IN1=0, IN2=1, IN3=0")
        side_layout.addWidget(self.flow_text, 1)

        self.btn_run_flow = QPushButton("Run Flow", self.side_panel)
        self.btn_run_flow.clicked.connect(self.run_flow_sequence)
        side_layout.addWidget(self.btn_run_flow)

        # Flow 輸出結果視窗
        side_layout.addWidget(QLabel("Flow outputs"))
        self.flow_log = QPlainTextEdit(self.side_panel)
        self.flow_log.setReadOnly(True)
        side_layout.addWidget(self.flow_log, 1)

        self.btn_plot_flow = QPushButton("Plot Flow", self.side_panel)
        self.btn_plot_flow.clicked.connect(self.plot_flow_waveforms)
        side_layout.addWidget(self.btn_plot_flow)
        hbox.addWidget(self.side_panel, 1)

        self.setCentralWidget(central)

        # 預設放兩個 IN、一個 OUT
        self.main_editor.add_gate("IN", x=50, y=120)
        self.main_editor.add_gate("IN", x=50, y=220)
        self.main_editor.add_gate("OUT", x=600, y=180)

        # 啟動後把 view 置中到現有的 items（用 singleShot 延遲到事件循環）
        QTimer.singleShot(0, self._center_view_on_items)

        # Toolbar
        self.toolbar = QToolBar("Main Toolbar", self)
        self.addToolBar(self.toolbar)

        self.flow_history = []   # 存每一步的 input / output 資料給畫圖用

        act_sim = QAction("Simulate", self)
        act_sim.triggered.connect(self.simulate_main_circuit)
        self.toolbar.addAction(act_sim)

        act_clock = QAction("Clock Tick", self)
        act_clock.triggered.connect(self.clock_tick)
        self.toolbar.addAction(act_clock)

        act_refresh = QAction("Refresh Inputs", self)
        act_refresh.triggered.connect(self.refresh_input_panel)
        self.toolbar.addAction(act_refresh)


        # Menu
        menubar = self.menuBar()
        menu_custom = menubar.addMenu("Custom")

        act_new = QAction("New Custom Gate...", self)
        act_new.triggered.connect(self.create_new_custom_gate)
        menu_custom.addAction(act_new)

        act_del = QAction("Delete Custom Gate...", self)
        act_del.triggered.connect(self.delete_custom_gate_dialog)
        menu_custom.addAction(act_del)

        # 雙擊 custom gate 進入 editor
        self.main_editor.scene.installEventFilter(self)

        self.statusBar().showMessage("Ready")
        # 啟動時自動把 Inputs / Outputs 列出來
        self.refresh_input_panel()

    
    # ---------- 右側參數面板：刷新列表 ----------
    def refresh_input_panel(self):
        # 先清空 layout 裡舊的 widget
        while self.input_list_layout.count():
            item = self.input_list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        # 找出所有 IN gate
        gates = [
            g for g in self.main_editor.scene.items()
            if isinstance(g, GateItem) and g.gate_type == "IN"
        ]
        # 依名稱排序，沒有名稱的排後面
        gates.sort(key=lambda g: g.param_name or "")

        for gate in gates:
            row = QWidget(self.input_list_container)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)

            name_edit = QLineEdit(row)
            name_edit.setText(gate.param_name or "IN")
            name_edit.setMaximumWidth(80)
            name_edit.setAlignment(Qt.AlignLeft)

            # 改名字
            def on_name_finished(g=gate, edit=name_edit):
                text = edit.text().strip()
                g.param_name = text if text else None
                g.update_display()

            name_edit.editingFinished.connect(on_name_finished)

            chk = QCheckBox("1", row)
            chk.setChecked(gate.value)

            # 改 value
            def on_state_changed(state, g=gate):
                g.value = (state == Qt.Checked)
                for k in range(g.output_count):
                    g.out_values[k] = g.value
                g.update_display()
                # 變更輸入後即時跑一次組合邏輯
                self.simulate_main_circuit()

            chk.stateChanged.connect(on_state_changed)

            row_layout.addWidget(name_edit)
            row_layout.addWidget(chk)
            self.input_list_layout.addWidget(row)

        self.input_list_layout.addStretch()

    def _center_view_on_items(self):
        try:
            br = self.main_editor.scene.itemsBoundingRect()
            if br.isNull() or (br.width() == 0 and br.height() == 0):
                return
            # 將 view 置中到 items 的中心，並確保可見範圍
            self.main_editor.view.centerOn(br.center())
            try:
                # 加入 margin，確保能看到周圍空間
                self.main_editor.view.ensureVisible(br, 50, 50)
            except Exception:
                pass
        except Exception:
            pass
    def plot_flow_waveforms(self):
        """
        開一個視窗，用目前 self.flow_history 畫 timing diagram。
        """
        if not getattr(self, "flow_history", None):
            QMessageBox.information(self, "No data", "請先按 Run Flow。")
            return
        if not self.flow_history:
            QMessageBox.information(self, "No data", "flow_history 目前是空的，請先按 Run Flow。")
            return

        # 保留一個參考避免被 GC
        self.wave_window = WaveformWindow(self.flow_history, None)
        self.wave_window.resize(900, 600)
        self.wave_window.show()

    def run_flow_sequence(self):
        """
        Flowchart 中每一行代表一個 clock 週期的「輸入設定」。
        步驟：
          1. 設好這一拍的所有 IN
          2. 以目前的 flip-flop 狀態模擬一次（組合邏輯）
          3. 把所有 OUT 值記錄下來（這一拍 clock 之前的狀態）
          4. 做一次 clock_tick()，更新 Q，準備下一拍
        """
        text = self.flow_text.toPlainText()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return

        # 建名字→IN gate 的 map
        in_gates = [
            g for g in self.main_editor.scene.items()
            if isinstance(g, GateItem) and g.gate_type == "IN"
        ]
        name_map: Dict[str, GateItem] = {}
        for g in in_gates:
            if g.param_name:
                name_map[g.param_name] = g

        # 先把所有 flip-flop 狀態清成 0
        gates = [g for g in self.main_editor.scene.items() if isinstance(g, GateItem)]
        for g in gates:
            if g.gate_type in ("DFF", "TFF"):
                g.ff_state = False
                if g.output_count > 0:
                    g.out_values[0] = False
                g.value = False

        # 清空舊結果
        self.flow_log.clear()
        self.flow_history = []

        for ln in lines:
            # ---- 1. 設定這一拍的輸入 ----
            assignments = [s.strip() for s in ln.split(",") if s.strip()]
            for assign in assignments:
                if "=" not in assign:
                    continue
                key, val = assign.split("=", 1)
                key = key.strip()
                val = val.strip()
                g = name_map.get(key)
                if g is None:
                    continue
                v = (val == "1" or val.lower() == "true")
                g.value = v
                for k in range(g.output_count):
                    g.out_values[k] = v
                g.update_display()

            # 這一拍的輸入 snapshot
            inputs_dict = {name: gate.value for name, gate in name_map.items()}

            # ---- 2. 以目前 Q 狀態模擬輸出（tick 前）----
            self.simulate_main_circuit()

            # ---- 3. 讀取所有 OUT，記錄在 log & history ----
            out_gates = [
                g for g in self.main_editor.scene.items()
                if isinstance(g, GateItem) and g.gate_type == "OUT"
            ]
            out_gates.sort(key=lambda g: g.param_name or "")

            parts = []
            outputs_dict = {}
            for idx, og in enumerate(out_gates):
                name = og.param_name or f"OUT{idx+1}"
                val_str = "1" if og.value else "0"
                parts.append(f"{name}={val_str}")
                outputs_dict[name] = og.value

            line_result = f"{ln}  ==>  " + ", ".join(parts)
            self.flow_log.appendPlainText(line_result)

            step_index = len(self.flow_history)
            self.flow_history.append({
                "step": step_index,
                "line": ln,
                "inputs": inputs_dict,
                "outputs": outputs_dict,
            })

            # ---- 4. tick 一次，更新到「下一拍」的 Q ----
            self.clock_tick()

            QApplication.processEvents()



    def refresh_output_panel(self):
        # 先清掉舊的 widget
        while self.output_list_layout.count():
            item = self.output_list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        # 找主畫面上的所有 OUT gate
        out_gates = [
            g for g in self.main_editor.scene.items()
            if isinstance(g, GateItem) and g.gate_type == "OUT"
        ]
        out_gates.sort(key=lambda g: g.param_name or "")

        for gate in out_gates:
            row = QWidget(self.output_list_container)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(2)

            name_edit = QLineEdit(row)
            name_edit.setText(gate.param_name or "OUT")
            name_edit.setMaximumWidth(80)
            name_edit.setAlignment(Qt.AlignLeft)   # ★ 靠左

            def on_name_finished(g=gate, edit=name_edit):
                text = edit.text().strip()
                g.param_name = text if text else None
                g.update_display()

            name_edit.editingFinished.connect(on_name_finished)

            row_layout.addWidget(name_edit)
            row_layout.addStretch(1)               # ★ 整排靠左
            self.output_list_layout.addWidget(row)

        self.output_list_layout.addStretch()

    # ---------- event filter：雙擊 custom gate ----------
    def eventFilter(self, obj, event):
        if obj is self.main_editor.scene and event.type() == QEvent.GraphicsSceneMouseDoubleClick:
            pos = event.scenePos()
            for item in self.main_editor.scene.items(pos):
                if isinstance(item, GateItem) and item.custom_def is not None:
                    self.open_custom_editor(item.custom_def)
                    return True
            return False
        return super().eventFilter(obj, event)

    # ---------- 建立新 custom gate ----------
    def create_new_custom_gate(self):
        dialog = QWidget(self, Qt.Window)
        dialog.setWindowTitle("New Custom Gate")

        layout = QVBoxLayout(dialog)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Gate Name:"))
        name_edit = QLineEdit("MyGate")
        name_row.addWidget(name_edit)
        layout.addLayout(name_row)

        in_row = QHBoxLayout()
        in_row.addWidget(QLabel("#Inputs:"))
        spin_in = QSpinBox()
        spin_in.setRange(1, 16)
        spin_in.setValue(2)
        in_row.addWidget(spin_in)
        layout.addLayout(in_row)

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("#Outputs:"))
        spin_out = QSpinBox()
        spin_out.setRange(1, 16)
        spin_out.setValue(1)
        out_row.addWidget(spin_out)
        layout.addLayout(out_row)

        btn_row = QHBoxLayout()
        btn_ok = QPushButton("OK")
        btn_cancel = QPushButton("Cancel")
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        def ok_clicked():
            name = name_edit.text().strip()
            if not name:
                QMessageBox.warning(dialog, "Error", "Gate name cannot be empty.")
                return
            if name in self.custom_defs or name in ("IN", "OUT", "AND", "OR", "NOT"):
                QMessageBox.warning(dialog, "Error", "Name already used.")
                return

            n_inputs = spin_in.value()
            n_outputs = spin_out.value()
            dialog.close()
            self._finish_create_custom_gate(name, n_inputs, n_outputs)

        btn_ok.clicked.connect(ok_clicked)
        btn_cancel.clicked.connect(dialog.close)

        dialog.setLayout(layout)
        dialog.setFixedSize(320, 200)
        dialog.show()

    def _finish_create_custom_gate(self, name: str, n_inputs: int, n_outputs: int):
        editor = CircuitEditorWidget(allow_io_edit=False)

        # 建立 IN gate
        y = 80
        for i in range(n_inputs):
            g = GateItem("IN", x=80, y=y)
            g.param_name = f"IN{i+1}"
            g.update_display()
            editor.scene.addItem(g)
            y += 70

        # 建立 OUT gate
        y = 80
        for i in range(n_outputs):
            g = GateItem("OUT", x=600, y=y)
            g.param_name = f"OUT{i+1}"
            g.update_display()
            editor.scene.addItem(g)
            y += 70

        defn = CustomGateDefinition(name, n_inputs, n_outputs, editor)
        self.custom_defs[name] = defn

        self.add_custom_gate_action(name)
        self.refresh_all_subeditors_toolbar()
        self.open_custom_editor(defn)

    # ---------- toolbar 上的 custom gate 按鈕 ----------
    def add_custom_gate_action(self, name: str):
        act = QAction(name, self)
        act.triggered.connect(lambda checked=False, n=name: self.add_custom_gate_to_main(n))
        self.toolbar.addAction(act)
        self.custom_gate_actions[name] = act

    def add_custom_gate_to_main(self, name: str):
        defn = self.custom_defs[name]
        g = GateItem(
            gate_type=name,
            x=200,
            y=200,
            input_count=defn.n_inputs,
            output_count=defn.n_outputs,
        )
        g.custom_def = defn
        g.update_display()
        self.main_editor.scene.addItem(g)

    # ---------- 刪除 custom gate ----------
    def delete_custom_gate_dialog(self):
        if not self.custom_defs:
            QMessageBox.information(self, "No Custom Gates", "No custom gates to delete.")
            return

        dialog = QWidget(self, Qt.Window)
        dialog.setWindowTitle("Delete Custom Gate")

        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Choose a custom gate to delete:"))

        for name in list(self.custom_defs.keys()):
            btn = QPushButton(name)
            btn.clicked.connect(lambda checked=False, n=name: self.delete_custom_gate(n, dialog))
            layout.addWidget(btn)

        dialog.setLayout(layout)
        dialog.resize(260, 200)
        dialog.show()

    def delete_custom_gate(self, name: str, dialog: QWidget):
        if name in self.custom_gate_actions:
            act = self.custom_gate_actions.pop(name)
            self.toolbar.removeAction(act)

        if name in self.custom_defs:
            self.custom_defs.pop(name)

        dialog.close()
        QMessageBox.information(self, "Deleted", f"Custom gate '{name}' deleted.")

        self.refresh_all_subeditors_toolbar()

    # ---------- 更新所有 editor 的 custom gate 工具列 ----------
    def refresh_all_subeditors_toolbar(self):
        self.main_editor.refresh_custom_gate_buttons(self.custom_defs)
        for defn in self.custom_defs.values():
            defn.editor.refresh_custom_gate_buttons(self.custom_defs)

    # ---------- 打開 custom gate editor ----------
    def open_custom_editor(self, defn: CustomGateDefinition):
        w = CustomGateEditorWindow(defn, self)
        w.resize(1000, 700)
        w.show()

    # ========================================================
    #              主電路模擬（含 custom gate）
    # ========================================================
    def clock_tick(self):
        """
        全域 clock tick（rising edge）：
        DFF: Q_next = 目前 D 的值
        TFF: 若 T=1 則 Q_next = not Q，否則維持原值
        （這裡只更新 Q，不負責記錄 flow；Run Flow 只在 tick 之前記錄輸出）
        """
        scene = self.main_editor.scene
        gates = [g for g in scene.items() if isinstance(g, GateItem)]

        next_state = {}

        # 先根據目前網路的 out_values 收集「下一拍的 Q」
        for g in gates:
            if g.gate_type == "DFF":
                w_d = g.input_wires[0] if g.input_count > 0 else None
                d = bool(w_d.start_gate.out_values[w_d.start_index]) if (w_d and w_d.start_gate) else False
                next_state[g] = d
            elif g.gate_type == "TFF":
                w_t = g.input_wires[0] if g.input_count > 0 else None
                t = bool(w_t.start_gate.out_values[w_t.start_index]) if (w_t and w_t.start_gate) else False
                if t:
                    next_state[g] = not g.ff_state
                else:
                    next_state[g] = g.ff_state

        # 再一次把 Q 更新，並寫回輸出
        for g, q in next_state.items():
            g.ff_state = q
            if g.output_count > 0:
                g.out_values[0] = q
            g.value = q

        # 給使用者按 toolbar「Clock Tick」時也會看到新狀態
        self.simulate_main_circuit()


    def simulate_main_circuit(self):
        scene = self.main_editor.scene
        gates = [g for g in scene.items() if isinstance(g, GateItem)]

        # 初始化
        for g in gates:
            if g.gate_type == "IN":
                for k in range(g.output_count):
                    g.out_values[k] = g.value
            else:
                for k in range(g.output_count):
                    g.out_values[k] = False
                g.value = False

        MAX_ITERS = 12
        for _ in range(MAX_ITERS):
            changed = False

            for g in gates:
                old = list(g.out_values)

                # IN gate
                if g.gate_type == "IN":
                    new_list = [g.value] * g.output_count

                # OUT gate
                elif g.gate_type == "OUT":
                    w = g.input_wires[0] if g.input_count > 0 else None
                    v = bool(w.start_gate.out_values[w.start_index]) if (w and w.start_gate) else False
                    g.value = v
                    new_list = []   # OUT 本身沒有輸出腳
                # DFF / TFF：組合邏輯階段只輸出目前的 ff_state
                elif g.gate_type in ("DFF", "TFF"):
                    new_list = [g.ff_state] * g.output_count
                
                elif g.gate_type == "JUNC":
                    w0 = g.input_wires[0] if g.input_count > 0 else None
                    new_val = bool(w0.start_gate.out_values[w0.start_index]) if (w0 and w0.start_gate) else False
                    new_list = [new_val] * g.output_count



                # custom gate
                elif g.custom_def is not None:
                    new_list = self.evaluate_custom_gate(g)

                # primitive gate
                else:
                    inputs = []
                    for i in range(g.input_count):
                        w = g.input_wires[i]
                        v = bool(w.start_gate.out_values[w.start_index]) if (w and w.start_gate) else False
                        inputs.append(v)

                    if g.gate_type == "AND":
                        new_val = all(inputs)
                    elif g.gate_type == "OR":
                        new_val = any(inputs)
                    elif g.gate_type == "NOT":
                        new_val = (not inputs[0]) if inputs else True
                    else:
                        new_val = False

                    new_list = [new_val] * g.output_count

                if new_list != old:
                    g.out_values = list(new_list)
                    if g.gate_type not in ("IN", "OUT") and g.output_count > 0:
                        g.value = g.out_values[0]
                    changed = True

            if not changed:
                break

        # 更新外觀
        for g in gates:
            g.update_display()
        self.refresh_output_panel()
        self.statusBar().showMessage("Simulation done")

    # ========================================================
    #           custom gate 評估（多輸出 + 巢狀）
    # ========================================================

    def evaluate_custom_gate(self, gate: GateItem) -> List[bool]:
        """
        計算一顆 custom gate 的所有輸出值，回傳長度 = n_outputs 的 list[bool]。
        """
        defn = gate.custom_def
        editor = defn.editor
        sub_gates: List[GateItem] = editor.all_gates()

        # 1. 外部輸入 -> 子電路 IN1, IN2, ...
        sub_in_nodes = [g for g in sub_gates if g.gate_type == "IN"]
        sub_in_nodes.sort(key=lambda n: n.param_name or "")

        for i, node in enumerate(sub_in_nodes):
            if i < gate.input_count:
                w = gate.input_wires[i]
                val = bool(w.start_gate.out_values[w.start_index]) if (w and w.start_gate) else False
            else:
                val = False
            node.value = val
            for k in range(node.output_count):
                node.out_values[k] = val

        # 2. 初始化其他 gate
        for sg in sub_gates:
            if sg.gate_type != "IN":
                sg.value = False
                for k in range(sg.output_count):
                    sg.out_values[k] = False

        # 3. 在子電路裡跑模擬迴圈（支援巢狀 custom gate）
        MAX_ITERS = 12
        for _ in range(MAX_ITERS):
            changed = False

            for sg in sub_gates:
                old = list(sg.out_values)

                if sg.gate_type == "IN":
                    new_list = [sg.value] * sg.output_count

                elif sg.gate_type == "OUT":
                    w = sg.input_wires[0] if sg.input_count > 0 else None
                    v = bool(w.start_gate.out_values[w.start_index]) if (w and w.start_gate) else False
                    sg.value = v
                    new_list = []   # OUT 本身沒有輸出腳
                
                elif sg.gate_type in ("DFF", "TFF"):
                    new_list = [sg.ff_state] * sg.output_count
                
                elif sg.gate_type == "JUNC":
                    w0 = sg.input_wires[0] if sg.input_count > 0 else None
                    new_val = bool(w0.start_gate.out_values[w0.start_index]) if (w0 and w0.start_gate) else False
                    new_list = [new_val] * sg.output_count



                elif sg.custom_def is not None:
                    # 巢狀 custom gate
                    new_list = self.evaluate_custom_gate(sg)

                else:
                    # primitive gate
                    inputs = []
                    for i in range(sg.input_count):
                        w = sg.input_wires[i]
                        v = bool(w.start_gate.out_values[w.start_index]) if (w and w.start_gate) else False
                        inputs.append(v)

                    if sg.gate_type == "AND":
                        new_val = all(inputs)
                    elif sg.gate_type == "OR":
                        new_val = any(inputs)
                    elif sg.gate_type == "NOT":
                        new_val = (not inputs[0]) if inputs else True
                    else:
                        new_val = False

                    new_list = [new_val] * sg.output_count

                if new_list != old:
                    sg.out_values = list(new_list)
                    if sg.gate_type not in ("IN", "OUT") and sg.output_count > 0:
                        sg.value = sg.out_values[0]
                    changed = True

            if not changed:
                break

        # 4. 子電路 OUT1..OUTn -> 這顆 custom gate 的 outputs
        sub_out_nodes = [g for g in sub_gates if g.gate_type == "OUT"]
        sub_out_nodes.sort(key=lambda n: n.param_name or "")

        result: List[bool] = []
        for i in range(defn.n_outputs):
            if i < len(sub_out_nodes):
                result.append(bool(sub_out_nodes[i].value))
            else:
                result.append(False)

        return result


# ============================================================
#                    Main Program Entry
# ============================================================

def main():
    app = QApplication(sys.argv)

    # 全域介面字型（選配）
    ui_font = QFont("Microsoft JhengHei")   # 或 "Arial", "Noto Sans CJK TC" 等
    ui_font.setPointSize(11)
    app.setFont(ui_font)

    win = MainWindow()
    win.show()
    sys.exit(app.exec_())



if __name__ == "__main__":
    main()