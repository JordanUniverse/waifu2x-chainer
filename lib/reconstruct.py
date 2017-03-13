from __future__ import division

from chainer import cuda
import numpy as np
from PIL import Image
import six

from lib import utils


def get_outer_padding(size, block_size, offset):
    pad = size % block_size
    if pad == 0:
        pad = offset
    else:
        pad = block_size - pad + offset
    return pad


def blockwise(src, model, block_size, batch_size):
    if src.ndim == 2:
        src = src[:, :, np.newaxis]
    h, w, ch = src.shape
    scale = 1. / 255.
    offset = model.offset
    xp = utils.get_model_module(model)
    ph = get_outer_padding(h, block_size, offset)
    pw = get_outer_padding(w, block_size, offset)
    psrc = np.pad(src, ((offset, ph), (offset, pw), (0, 0)), 'edge')
    nh = (psrc.shape[0] - offset * 2) // block_size
    nw = (psrc.shape[1] - offset * 2) // block_size

    psrc = psrc.transpose(2, 0, 1)
    block_offset = block_size + offset * 2
    x = np.zeros((nh * nw, ch, block_offset, block_offset), dtype=np.uint8)
    for i in range(0, nh):
        ih = i * block_size
        for j in range(0, nw):
            jw = j * block_size
            psrc_ij = psrc[:, ih:ih + block_offset, jw:jw + block_offset]
            x[(i * nw) + j, :, :, :] = psrc_ij

    y = np.zeros((nh * nw, ch, block_size, block_size), dtype=np.float32)
    for i in range(0, nh * nw, batch_size):
        batch_x = xp.array(x[i:i + batch_size], dtype=np.float32) * scale
        batch_y = model(batch_x)
        y[i:i + batch_size] = cuda.to_cpu(batch_y.data)

    dst = np.zeros((ch, h + ph, w + pw), dtype=np.float32)
    for i in range(0, nh):
        ih = i * block_size
        for j in range(0, nw):
            jw = j * block_size
            dst[:, ih:ih + block_size, jw:jw + block_size] = y[(i * nw) + j]

    dst = dst[:, :h, :w]
    return dst.transpose(1, 2, 0)


def inv(rot, flip=False):
    if flip:
        return lambda x: np.rot90(x, rot // 90, axes=(0, 1))[:, ::-1, :]
    else:
        return lambda x: np.rot90(x, rot // 90, axes=(0, 1))


def get_tta_patterns(src, n):
    src_lr = src.transpose(Image.FLIP_LEFT_RIGHT)
    patterns = [
        [src, None],
        [src.transpose(Image.ROTATE_90), inv(-90)],
        [src.transpose(Image.ROTATE_180), inv(-180)],
        [src.transpose(Image.ROTATE_270), inv(-270)],
        [src_lr, inv(0, True)],
        [src_lr.transpose(Image.ROTATE_90), inv(-90, True)],
        [src_lr.transpose(Image.ROTATE_180), inv(-180, True)],
        [src_lr.transpose(Image.ROTATE_270), inv(-270, True)],
    ]
    if n == 2:
        return [patterns[0], patterns[4]]
    elif n == 4:
        return [patterns[0], patterns[2], patterns[4], patterns[6]]
    elif n == 8:
        return patterns
    return [patterns[0]]


def image_tta(src, model, scale, tta_level, block_size, batch_size):
    if scale:
        src = src.resize((src.size[0] * 2, src.size[1] * 2), Image.NEAREST)
    patterns = get_tta_patterns(src, tta_level)
    dst = np.zeros((src.size[1], src.size[0], 3))
    cbcr = np.zeros((src.size[1], src.size[0], 2))
    if model.ch == 1:
        for i, (pat, inv) in enumerate(patterns):
            six.print_(i, end=' ', flush=True)
            pat = np.array(pat.convert('YCbCr'), dtype=np.uint8)
            if i == 0:
                cbcr = pat[:, :, 1:]
            tmp = blockwise(pat[:, :, 0], model, block_size, batch_size)
            if inv is not None:
                tmp = inv(tmp)
            dst[:, :, 0] += tmp[:, :, 0]
        dst /= len(patterns)
        dst = np.clip(dst, 0, 1) * 255
        dst[:, :, 1:] = cbcr
        dst = dst.astype(np.uint8)
        dst = Image.fromarray(dst, mode='YCbCr').convert('RGB')
    elif model.ch == 3:
        for i, (pat, inv) in enumerate(patterns):
            six.print_(i, end=' ', flush=True)
            pat = np.array(pat.convert('RGB'), dtype=np.uint8)
            tmp = blockwise(pat, model, block_size, batch_size)
            if inv is not None:
                tmp = inv(tmp)
            dst += tmp
        dst /= len(patterns)
        dst = np.clip(dst, 0, 1) * 255
        dst = Image.fromarray(dst.astype(np.uint8))
    return dst


def image(src, model, scale, block_size, batch_size):
    if scale:
        src = src.resize((src.size[0] * 2, src.size[1] * 2), Image.NEAREST)
    if model.ch == 1:
        src = np.array(src.convert('YCbCr'), dtype=np.uint8)
        dst = blockwise(src[:, :, 0], model, block_size, batch_size)
        dst = np.clip(dst, 0, 1) * 255
        src[:, :, 0] = dst[:, :, 0]
        dst = Image.fromarray(src, mode='YCbCr').convert('RGB')
    elif model.ch == 3:
        src = np.array(src.convert('RGB'), dtype=np.uint8)
        dst = blockwise(src, model, block_size, batch_size)
        dst = np.clip(dst, 0, 1) * 255
        dst = Image.fromarray(dst.astype(np.uint8))
    return dst
