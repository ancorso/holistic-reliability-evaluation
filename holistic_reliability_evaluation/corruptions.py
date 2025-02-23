# Modified from https://github.com/hendrycks/robustness/blob/master/ImageNet-C/create_c/make_imagenet_c.py

# /////////////// Distortion Helpers ///////////////
import os
import skimage as sk
from skimage.filters import gaussian
from io import BytesIO
from wand.image import Image as WandImage
from wand.api import library as wandlibrary
import wand.color as WandColor
import ctypes
from PIL import Image as PILImage
import cv2
from scipy.ndimage import zoom as scizoom
from scipy.ndimage.interpolation import map_coordinates
import numpy as np
import torchvision.transforms as transforms
import math


def disk(radius, alias_blur=0.1, dtype=np.float32):
    if radius <= 8:
        L = np.arange(-8, 8 + 1)
        ksize = (3, 3)
    else:
        L = np.arange(-radius, radius + 1)
        ksize = (5, 5)
    X, Y = np.meshgrid(L, L)
    aliased_disk = np.array((X ** 2 + Y ** 2) <= radius ** 2, dtype=dtype)
    aliased_disk /= np.sum(aliased_disk)

    # supersample disk to antialias
    return cv2.GaussianBlur(aliased_disk, ksize=ksize, sigmaX=alias_blur)


# Tell Python about the C method
wandlibrary.MagickMotionBlurImage.argtypes = (ctypes.c_void_p,  # wand
                                              ctypes.c_double,  # radius
                                              ctypes.c_double,  # sigma
                                              ctypes.c_double)  # angle


# Extend wand.image.Image class to include method signature
class MotionImage(WandImage):
    def motion_blur(self, radius=0.0, sigma=0.0, angle=0.0):
        wandlibrary.MagickMotionBlurImage(self.wand, radius, sigma, angle)

def smallest_power_of_two_greater_than(n):
    if n <= 0:
        raise ValueError("Input value must be greater than 0")
    exponent = math.ceil(math.log2(n))
    return 2 ** exponent

# modification of https://github.com/FLHerne/mapgen/blob/master/diamondsquare.py
def plasma_fractal(mapsize=256, wibbledecay=3):
    mapsize = smallest_power_of_two_greater_than(mapsize)
    """
    Generate a heightmap using diamond-square algorithm.
    Return square 2d array, side length 'mapsize', of floats in range 0-255.
    'mapsize' must be a power of two.
    """
    assert (mapsize & (mapsize - 1) == 0)
    maparray = np.empty((mapsize, mapsize), dtype=np.float_)
    maparray[0, 0] = 0
    stepsize = mapsize
    wibble = 100

    def wibbledmean(array):
        return array / 4 + wibble * np.random.uniform(-wibble, wibble, array.shape)

    def fillsquares():
        """For each square of points stepsize apart,
           calculate middle value as mean of points + wibble"""
        cornerref = maparray[0:mapsize:stepsize, 0:mapsize:stepsize]
        squareaccum = cornerref + np.roll(cornerref, shift=-1, axis=0)
        squareaccum += np.roll(squareaccum, shift=-1, axis=1)
        maparray[stepsize // 2:mapsize:stepsize,
        stepsize // 2:mapsize:stepsize] = wibbledmean(squareaccum)

    def filldiamonds():
        """For each diamond of points stepsize apart,
           calculate middle value as mean of points + wibble"""
        mapsize = maparray.shape[0]
        drgrid = maparray[stepsize // 2:mapsize:stepsize, stepsize // 2:mapsize:stepsize]
        ulgrid = maparray[0:mapsize:stepsize, 0:mapsize:stepsize]
        ldrsum = drgrid + np.roll(drgrid, 1, axis=0)
        lulsum = ulgrid + np.roll(ulgrid, -1, axis=1)
        ltsum = ldrsum + lulsum
        maparray[0:mapsize:stepsize, stepsize // 2:mapsize:stepsize] = wibbledmean(ltsum)
        tdrsum = drgrid + np.roll(drgrid, 1, axis=1)
        tulsum = ulgrid + np.roll(ulgrid, -1, axis=0)
        ttsum = tdrsum + tulsum
        maparray[stepsize // 2:mapsize:stepsize, 0:mapsize:stepsize] = wibbledmean(ttsum)

    while stepsize >= 2:
        fillsquares()
        filldiamonds()
        stepsize //= 2
        wibble /= wibbledecay

    maparray -= maparray.min()
    return maparray / maparray.max()


def clipped_zoom(img, zoom_factor):
    h, w = img.shape[0], img.shape[1]
    # ceil crop height(= crop width)
    ch = int(np.ceil(h / zoom_factor))
    cw = int(np.ceil(w / zoom_factor))

    toph = (h - ch) // 2
    topw = (w - cw) // 2
    img = scizoom(img[toph:toph + ch, topw:topw + cw], (zoom_factor, zoom_factor, 1), order=1)
    # trim off any extra pixels
    trim_toph = (img.shape[0] - h) // 2
    trim_topw = (img.shape[1] - w) // 2

    return img[trim_toph:trim_toph + h, trim_topw:trim_topw + w]


# /////////////// End Distortion Helpers ///////////////


# /////////////// Distortions ///////////////

def to_image(x):
    return PILImage.fromarray(np.uint8(np.clip(x, 0, 1)*255))

def to_image_from_0_255(x):
    return PILImage.fromarray(np.uint8(np.clip(x, 0, 255)))

def gaussian_noise(x, severity=1):
    c = [.08, .12, 0.18, 0.26, 0.38][severity - 1]

    x = np.array(x) / 255.
    return to_image(x + np.random.normal(size=x.shape, scale=c))


def shot_noise(x, severity=1):
    c = [60, 25, 12, 5, 3][severity - 1]

    x = np.array(x) / 255.
    return to_image(np.random.poisson(x * c) / c)


def impulse_noise(x, severity=1):
    c = [.03, .06, .09, 0.17, 0.27][severity - 1]

    x = sk.util.random_noise(np.array(x) / 255., mode='s&p', amount=c)
    return to_image(x)


def speckle_noise(x, severity=1):
    c = [.15, .2, 0.35, 0.45, 0.6][severity - 1]

    x = np.array(x) / 255.
    return to_image(x + x * np.random.normal(size=x.shape, scale=c))

def gaussian_blur(x, severity=1):
    c = [1, 2, 3, 4, 6][severity - 1]

    x = gaussian(np.array(x) / 255., sigma=c, channel_axis=2)
    return to_image(x)


def glass_blur(x, severity=1):
    # sigma, max_delta, iterations
    c = [(0.7, 1, 2), (0.9, 2, 1), (1, 2, 3), (1.1, 3, 2), (1.5, 4, 2)][severity - 1]

    x = np.uint8(gaussian(np.array(x) / 255., sigma=c[0], channel_axis=2) * 255)

    # locally shuffle pixels
    for i in range(c[2]):
        for h in range(x.shape[0] - c[1], c[1], -1):
            for w in range(x.shape[1] - c[1], c[1], -1):
                dx, dy = np.random.randint(-c[1], c[1], size=(2,))
                h_prime, w_prime = h + dy, w + dx
                # swap
                x[h, w], x[h_prime, w_prime] = x[h_prime, w_prime], x[h, w]

    return to_image(gaussian(x / 255., sigma=c[0], channel_axis=2))


def defocus_blur(x, severity=1):
    c = [(3, 0.1), (4, 0.5), (6, 0.5), (8, 0.5), (10, 0.5)][severity - 1]

    x = np.array(x) / 255.
    kernel = disk(radius=c[0], alias_blur=c[1])

    channels = []
    for d in range(3):
        channels.append(cv2.filter2D(x[:, :, d], -1, kernel))
    channels = np.array(channels).transpose((1, 2, 0))  # 3xWxH -> WxHx3

    return to_image(channels)


def motion_blur(x, severity=1):
    c = [(10, 3), (15, 5), (15, 8), (15, 12), (20, 15)][severity - 1]
    
    sz = x.size

    output = BytesIO()
    x.save(output, format='PNG')
    x = MotionImage(blob=output.getvalue())

    x.motion_blur(radius=c[0], sigma=c[1], angle=np.random.uniform(-45, 45))

    x = cv2.imdecode(np.frombuffer(x.make_blob(), np.uint8),
                     cv2.IMREAD_UNCHANGED)
    
    if len(x.shape) == 3:
        return to_image_from_0_255(x[..., [2, 1, 0]])
    else:  # greyscale to RGB
        return to_image_from_0_255(np.array([x, x, x]).transpose((1, 2, 0)))


def zoom_blur(x, severity=1):
    c = [np.arange(1, 1.11, 0.01),
         np.arange(1, 1.16, 0.01),
         np.arange(1, 1.21, 0.02),
         np.arange(1, 1.26, 0.02),
         np.arange(1, 1.31, 0.03)][severity - 1]

    x = (np.array(x) / 255.).astype(np.float32)
    out = np.zeros_like(x)
    for zoom_factor in c:
        out += clipped_zoom(x, zoom_factor)

    x = (x + out) / (len(c) + 1)
    return to_image(x)


def fog(x, severity=1):
    c = [(1.5, 2), (2, 2), (2.5, 1.7), (2.5, 1.5), (3, 1.4)][severity - 1]

    x = np.array(x) / 255.
    max_val = x.max()
    x += c[0] * plasma_fractal(mapsize=max(x.shape[0], x.shape[1]), wibbledecay=c[1])[:x.shape[0], :x.shape[1]][..., np.newaxis]
    return to_image(x * max_val / (max_val + c[0]))


def frost(x, severity=1):
    c = [(1, 0.4),
         (0.8, 0.6),
         (0.7, 0.7),
         (0.65, 0.7),
         (0.6, 0.75)][severity - 1]
    idx = np.random.randint(5)
    filename = ['frost1.png', 'frost2.png', 'frost3.png', 'frost4.jpg', 'frost5.jpg', 'frost6.jpg'][idx]
    frost = cv2.imread(os.path.join(os.path.dirname(__file__), "frosts", filename))
    
    # In case we are dealing with images that are larger than the frost image, resize
    new_size_wh = (max(frost.shape[1], x.size[0]+1), max(frost.shape[0], x.size[1]+1))
    frost = cv2.resize(frost, new_size_wh)

    # randomly crop and convert to rgb
    h_start, w_start = np.random.randint(0, frost.shape[0] - x.size[1]), np.random.randint(0, frost.shape[1] - x.size[0])
    frost = frost[h_start:h_start + x.size[1], w_start:w_start + x.size[0]][..., [2, 1, 0]]

    return to_image_from_0_255(c[0] * np.array(x) + c[1] * frost)


def snow(x, severity=1):
    c = [(0.1, 0.3, 3, 0.5, 10, 4, 0.8),
         (0.2, 0.3, 2, 0.5, 12, 4, 0.7),
         (0.55, 0.3, 4, 0.9, 12, 8, 0.7),
         (0.55, 0.3, 4.5, 0.85, 12, 8, 0.65),
         (0.55, 0.3, 2.5, 0.85, 12, 12, 0.55)][severity - 1]

    x = np.array(x, dtype=np.float32) / 255.
    snow_layer = np.random.normal(size=x.shape[:2], loc=c[0], scale=c[1])  # [:2] for monochrome

    snow_layer = clipped_zoom(snow_layer[..., np.newaxis], c[2])
    snow_layer[snow_layer < c[3]] = 0

    snow_layer = PILImage.fromarray((np.clip(snow_layer.squeeze(), 0, 1) * 255).astype(np.uint8), mode='L')
    output = BytesIO()
    snow_layer.save(output, format='PNG')
    snow_layer = MotionImage(blob=output.getvalue())

    snow_layer.motion_blur(radius=c[4], sigma=c[5], angle=np.random.uniform(-135, -45))

    snow_layer = cv2.imdecode(np.frombuffer(snow_layer.make_blob(), np.uint8),
                              cv2.IMREAD_UNCHANGED) / 255.
    snow_layer = snow_layer[..., np.newaxis]

    x = c[6] * x + (1 - c[6]) * np.maximum(x, cv2.cvtColor(x, cv2.COLOR_RGB2GRAY).reshape(x.shape[0], x.shape[1], 1) * 1.5 + 0.5)
    return to_image(x + snow_layer + np.rot90(snow_layer, k=2))


def spatter(x, severity=1):
    c = [(0.65, 0.3, 4, 0.69, 0.6, 0),
         (0.65, 0.3, 3, 0.68, 0.6, 0),
         (0.65, 0.3, 2, 0.68, 0.5, 0),
         (0.65, 0.3, 1, 0.65, 1.5, 1),
         (0.67, 0.4, 1, 0.65, 1.5, 1)][severity - 1]
    x = np.array(x, dtype=np.float32) / 255.

    liquid_layer = np.random.normal(size=x.shape[:2], loc=c[0], scale=c[1])

    liquid_layer = gaussian(liquid_layer, sigma=c[2])
    liquid_layer[liquid_layer < c[3]] = 0
    if c[5] == 0:
        liquid_layer = (liquid_layer * 255).astype(np.uint8)
        dist = 255 - cv2.Canny(liquid_layer, 50, 150)
        dist = cv2.distanceTransform(dist, cv2.DIST_L2, 5)
        _, dist = cv2.threshold(dist, 20, 20, cv2.THRESH_TRUNC)
        dist = cv2.blur(dist, (3, 3)).astype(np.uint8)
        dist = cv2.equalizeHist(dist)
        #     ker = np.array([[-1,-2,-3],[-2,0,0],[-3,0,1]], dtype=np.float32)
        #     ker -= np.mean(ker)
        ker = np.array([[-2, -1, 0], [-1, 1, 1], [0, 1, 2]])
        dist = cv2.filter2D(dist, cv2.CV_8U, ker)
        dist = cv2.blur(dist, (3, 3)).astype(np.float32)

        m = cv2.cvtColor(liquid_layer * dist, cv2.COLOR_GRAY2BGRA)
        m /= np.max(m, axis=(0, 1))
        m *= c[4]

        # water is pale turqouise
        color = np.concatenate((175 / 255. * np.ones_like(m[..., :1]),
                                238 / 255. * np.ones_like(m[..., :1]),
                                238 / 255. * np.ones_like(m[..., :1])), axis=2)

        color = cv2.cvtColor(color, cv2.COLOR_BGR2BGRA)
        x = cv2.cvtColor(x, cv2.COLOR_BGR2BGRA)

        return to_image(cv2.cvtColor(np.clip(x + m * color, 0, 1), cv2.COLOR_BGRA2BGR))
    else:
        m = np.where(liquid_layer > c[3], 1, 0)
        m = gaussian(m.astype(np.float32), sigma=c[4])
        m[m < 0.8] = 0
        #         m = np.abs(m) ** (1/c[4])

        # mud brown
        color = np.concatenate((63 / 255. * np.ones_like(x[..., :1]),
                                42 / 255. * np.ones_like(x[..., :1]),
                                20 / 255. * np.ones_like(x[..., :1])), axis=2)

        color *= m[..., np.newaxis]
        x *= (1 - m[..., np.newaxis])

        return to_image(x + color)


def contrast(x, severity=1):
    c = [0.4, .3, .2, .1, .05][severity - 1]

    x = np.array(x) / 255.
    means = np.mean(x, axis=(0, 1), keepdims=True)
    return to_image((x - means) * c + means)


def brightness(x, severity=1):
    c = [.1, .2, .3, .4, .5][severity - 1]

    x = np.array(x) / 255.
    x = sk.color.rgb2hsv(x)
    x[:, :, 2] = np.clip(x[:, :, 2] + c, 0, 1)
    x = sk.color.hsv2rgb(x)

    return to_image(x)

def saturate(x, severity=1):
    c = [(0.3, 0), (0.1, 0), (2, 0), (5, 0.1), (20, 0.2)][severity - 1]

    x = np.array(x) / 255.
    x = sk.color.rgb2hsv(x)
    x[:, :, 1] = np.clip(x[:, :, 1] * c[0] + c[1], 0, 1)
    x = sk.color.hsv2rgb(x)

    return to_image(x)


def jpeg_compression(x, severity=1):
    c = [25, 18, 15, 10, 7][severity - 1]

    output = BytesIO()
    x.save(output, 'JPEG', quality=c)
    return PILImage.open(output)


def pixelate(x, severity=1):
    c = [0.6, 0.5, 0.4, 0.3, 0.25][severity - 1]
    sz = x.size
    x = x.resize((int(sz[0] * c), int(sz[1] * c)), PILImage.BOX)
    return x.resize(sz, PILImage.BOX)


# mod of https://gist.github.com/erniejunior/601cdf56d2b424757de5
def elastic_transform(image, severity=1):
    width = image.size[0]
    c = [(width * 2, width * 0.7, width * 0.1),  
         (width * 2, width * 0.08, width * 0.2),
         (width * 0.05, width * 0.01, width * 0.02),
         (width * 0.07, width * 0.01, width * 0.02),
         (width * 0.12, width * 0.01, width * 0.02)][severity - 1]

    image = np.array(image, dtype=np.float32) / 255.
    shape = image.shape
    shape_size = shape[:2]

    # random affine
    center_square = np.float32(shape_size) // 2
    square_size = min(shape_size) // 3
    pts1 = np.float32([center_square + square_size,
                       [center_square[0] + square_size, center_square[1] - square_size],
                       center_square - square_size])
    pts2 = pts1 + np.random.uniform(-c[2], c[2], size=pts1.shape).astype(np.float32)
    M = cv2.getAffineTransform(pts1, pts2)
    image = cv2.warpAffine(image, M, shape_size[::-1], borderMode=cv2.BORDER_REFLECT_101)

    dx = (gaussian(np.random.uniform(-1, 1, size=shape[:2]),
                   c[1], mode='reflect', truncate=3) * c[0]).astype(np.float32)
    dy = (gaussian(np.random.uniform(-1, 1, size=shape[:2]),
                   c[1], mode='reflect', truncate=3) * c[0]).astype(np.float32)
    dx, dy = dx[..., np.newaxis], dy[..., np.newaxis]

    x, y, z = np.meshgrid(np.arange(shape[1]), np.arange(shape[0]), np.arange(shape[2]))
    indices = np.reshape(y + dy, (-1, 1)), np.reshape(x + dx, (-1, 1)), np.reshape(z, (-1, 1))
    return to_image(map_coordinates(image, indices, order=1, mode='reflect').reshape(shape))


# /////////////// End Distortions ///////////////

def all_corruptions(severity=1):
    corruption_transforms = [
        transforms.Lambda(lambda x: gaussian_noise(x, severity)),
        transforms.Lambda(lambda x: shot_noise(x, severity)),
        transforms.Lambda(lambda x: impulse_noise(x, severity)), 
        transforms.Lambda(lambda x: defocus_blur(x, severity)),
        transforms.Lambda(lambda x: glass_blur(x, severity)),
        transforms.Lambda(lambda x: motion_blur(x, severity)), 
        transforms.Lambda(lambda x: zoom_blur(x, severity)),
        transforms.Lambda(lambda x: snow(x, severity)),
        transforms.Lambda(lambda x: frost(x, severity)), 
        transforms.Lambda(lambda x: fog(x, severity)),
        transforms.Lambda(lambda x: brightness(x, severity)),
        transforms.Lambda(lambda x: contrast(x, severity)),
        transforms.Lambda(lambda x: elastic_transform(x, severity)),
        transforms.Lambda(lambda x: pixelate(x, severity)),
        transforms.Lambda(lambda x: jpeg_compression(x, severity)),
        transforms.Lambda(lambda x: speckle_noise(x, severity)),
        transforms.Lambda(lambda x: gaussian_blur(x, severity)), 
        transforms.Lambda(lambda x: spatter(x, severity)),
        transforms.Lambda(lambda x: saturate(x, severity))
    ]
    return corruption_transforms

def validation_corruptions(severity=1):
    np.random.seed(0)
    corruptions = all_corruptions(severity)
    np.random.shuffle(corruptions)
    half = len(corruptions) // 2
    return corruptions[:half]

def test_corruptions(severity=1):
    np.random.seed(0)
    corruptions = all_corruptions(severity)
    np.random.shuffle(corruptions)
    half = len(corruptions) // 2
    return corruptions[half:]

def plot_corrupted_image(x, severity=1):
    import matplotlib.pyplot as plt
    plt.imshow(gaussian_noise(x, severity))
    plt.savefig("gaussian_noise.png")

    plt.imshow(shot_noise(x, severity))
    plt.savefig("shot_noise.png")

    plt.imshow(impulse_noise(x, severity))
    plt.savefig("impulse_noise.png")

    plt.imshow(defocus_blur(x, severity))
    plt.savefig("defocus_blur.png")

    plt.imshow(glass_blur(x, severity))
    plt.savefig("glass_blur.png")

    plt.imshow(motion_blur(x, severity))
    plt.savefig("motion_blur.png")

    plt.imshow(zoom_blur(x, severity))
    plt.savefig("zoom_blur.png")

    plt.imshow(snow(x, severity))
    plt.savefig("snow.png")

    plt.imshow(frost(x, severity))
    plt.savefig("frost.png")

    plt.imshow(fog(x, severity))
    plt.savefig("fog.png")

    plt.imshow(brightness(x, severity))
    plt.savefig("brightness.png")

    plt.imshow(contrast(x, severity))
    plt.savefig("contrast.png")

    plt.imshow(elastic_transform(x, severity))
    plt.savefig("elastic_transform.png")

    plt.imshow(pixelate(x, severity))
    plt.savefig("pixelate.png")

    plt.imshow(jpeg_compression(x, severity))
    plt.savefig("jpeg_compression.png")

    plt.imshow(speckle_noise(x, severity))
    plt.savefig("speckle_noise.png")

    plt.imshow(gaussian_blur(x, severity))
    plt.savefig("gaussian_blur.png")

    plt.imshow(spatter(x, severity))
    plt.savefig("spatter.png")

    plt.imshow(saturate(x, severity))
    plt.savefig("saturate.png")


## This is some code to run all the corruptions on a bunch of wilds datasets which feature gray-scale and multi-size images
# import wilds
# import torch
# import torchvision.transforms as tfs
# dataset = wilds.get_dataset(dataset="iwildcam", root_dir="/scratch/users/acorso/data")

# transforms_list = []
# transforms_list.append(tfs.Resize([448,448]))
# transforms_list.append(tfs.ToTensor())
# transforms_list.append(tfs.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]))

# tf = transforms.Compose(transforms_list)

# for i in np.random.permutation(len(dataset)):
#     print("index: ", i)
#     x, y, md = dataset[i]
#     j=0
#     for c in all_corruptions():
#         print("corruption: ", j)
#         j = j+1
#         xc = c(x)
#         tensor = tf(xc)
#         print(tensor.shape)
#         if tensor.shape[0] == 1:
#             print("got one at ", i, " with ", c)
#             exit()
        