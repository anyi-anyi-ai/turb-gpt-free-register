from oxymouse.algorithms.bezier_mouse.bezier_mouse import BezierMouse
from oxymouse.algorithms.gaussian_mouse.gaussian_mouse import GaussianMouse
from oxymouse.algorithms.oxy.oxy_mouse import OxyMouse
from oxymouse.algorithms.perlin_mouse.perlin_mouse import PerlinMouse

mouses = {"perlin": PerlinMouse(), "bezier": BezierMouse(), "gaussian": GaussianMouse(), "oxy": OxyMouse()}
