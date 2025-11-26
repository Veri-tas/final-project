import sys
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QGraphicsView, QGraphicsScene,
    QGraphicsRectItem, QGraphicsSimpleTextItem, QToolBar, QAction
)
from PyQt5.QtGui import QBrush, QPen,QPainter
from PyQt5.QtCore import Qt, QRectF


class GateItem(QGraphicsRectItem):
    """一個簡單的邏輯閘圖形物件，可拖曳、可被選取。"""

    def __init__(self, gate_type="AND", x=0, y=0, w=80, h=50):
        super().__init__(0, 0, w, h)
        self.gate_type = gate_type

        # 外觀
        self.setBrush(QBrush(Qt.white))
        self.setPen(QPen(Qt.black, 2))
        self.setFlags(
            QGraphicsRectItem.ItemIsMovable |
            QGraphicsRectItem.ItemIsSelectable |
            QGraphicsRectItem.ItemSendsGeometryChanges
        )

        # 顯示文字
        self.label = QGraphicsSimpleTextItem(gate_type, self)
        # 讓文字大概在中間
        b = self.label.boundingRect()
        self.label.setPos((w - b.width()) / 2, (h - b.height()) / 2)

        # 起始位置
        self.setPos(x, y)

    def boundingRect(self) -> QRectF:
        # 可以保留父類別的實作
        return super().boundingRect()

    # 之後可以在這裡加：輸入/輸出 pin 的座標計算函式
    # def input_pins(self): ...
    # def output_pin(self): ...


class LogicEditorView(QGraphicsView):
    """顯示邏輯電路的 View，未來可以在這裡加上畫線、縮放、框選等功能。"""

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)  # 可以框選多個 gate
        self.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Python Logic Gate Editor (Mini Quartus Style)")
        self.resize(800, 600)

        # 建立場景 & 視圖
        self.scene = QGraphicsScene(self)
        self.scene.setSceneRect(0, 0, 2000, 2000)  # 大一點的畫布
        self.view = LogicEditorView(self.scene, self)
        self.setCentralWidget(self.view)

        # 加一個 toolbar，用來新增 gate
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

        # 一開始先放幾個 gate 看看
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