from ultralytics import YOLO
import cv2

model = YOLO("yolov8s.pt")
model.train(data="defects.yaml", epochs=80, imgsz=1024, close_mosaic=10)


def preprocess(path: str):
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(img)
