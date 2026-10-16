from PyQt6.QtWidgets import QLabel, QMainWindow


class MainWindow(QMainWindow):
    """Minimal application shell for the initial project milestone."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Flow Automation Tool")
        self.resize(960, 640)
        self.setCentralWidget(QLabel("Flow Automation Tool"))

