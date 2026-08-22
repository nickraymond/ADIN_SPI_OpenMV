"""YOLOX-Nano (conv-stem variant) -- the S8 bite E gated architecture.

Exactly the geometry that passed the 2026-08-22 compile gate
(ml/compile_gate_report.md): stock YOLOX-Nano with the Focus stem
replaced by a plain stride-2 conv (Vela: stride-1 STRIDED_SLICE only),
single class. Export uses raw per-level head outputs (decode on-board).

Requires ~/nereus_ml/third_party/YOLOX (Apache-2.0, sha recorded in the
gate run metadata) on sys.path -- add_yolox_path() does that.
"""
import sys
from pathlib import Path

import torch

YOLOX_DIR = Path.home() / "nereus_ml" / "third_party" / "YOLOX"


def add_yolox_path():
    if str(YOLOX_DIR) not in sys.path:
        sys.path.insert(0, str(YOLOX_DIR))


def _patch_head_for_mps():
    """YOLOX's get_output_and_grid casts its grid with a legacy dtype STRING
    (`xin[0].type()` -> 'torch.mps.FloatTensor'), which torch rejects on MPS.
    Replace it with a device-aware equivalent; the only behavior change is
    how the cached grid tensor is materialized. Third_party stays unpatched.
    """
    from yolox.models.yolo_head import YOLOXHead
    from yolox.utils import meshgrid

    def get_output_and_grid(self, output, k, stride, dtype):
        grid = self.grids[k]
        batch_size = output.shape[0]
        n_ch = 5 + self.num_classes
        hsize, wsize = output.shape[-2:]
        if grid.shape[2:4] != output.shape[2:4]:
            yv, xv = meshgrid([torch.arange(hsize), torch.arange(wsize)])
            grid = (torch.stack((xv, yv), 2).view(1, 1, hsize, wsize, 2)
                    .to(output.device, output.dtype))
            self.grids[k] = grid
        output = output.view(batch_size, 1, n_ch, hsize, wsize)
        output = output.permute(0, 1, 3, 4, 2).reshape(
            batch_size, hsize * wsize, -1)
        grid = grid.view(1, -1, 2).to(output.device, output.dtype)
        output = output.clone()
        output[..., :2] = (output[..., :2] + grid) * stride
        output[..., 2:4] = torch.exp(output[..., 2:4]) * stride
        return output, grid

    YOLOXHead.get_output_and_grid = get_output_and_grid

    # Same legacy `.type(tensor.type())` string cast inside bboxes_iou
    # (SimOTA's pairwise IoU). Patch the source module AND yolo_head's
    # imported-by-name copy.
    import yolox.models.yolo_head as _yh
    import yolox.utils.boxes as _bx

    def bboxes_iou(bboxes_a, bboxes_b, xyxy=True):
        if bboxes_a.shape[1] != 4 or bboxes_b.shape[1] != 4:
            raise IndexError
        if xyxy:
            tl = torch.max(bboxes_a[:, None, :2], bboxes_b[:, :2])
            br = torch.min(bboxes_a[:, None, 2:], bboxes_b[:, 2:])
            area_a = torch.prod(bboxes_a[:, 2:] - bboxes_a[:, :2], 1)
            area_b = torch.prod(bboxes_b[:, 2:] - bboxes_b[:, :2], 1)
        else:
            tl = torch.max(bboxes_a[:, None, :2] - bboxes_a[:, None, 2:] / 2,
                           bboxes_b[:, :2] - bboxes_b[:, 2:] / 2)
            br = torch.min(bboxes_a[:, None, :2] + bboxes_a[:, None, 2:] / 2,
                           bboxes_b[:, :2] + bboxes_b[:, 2:] / 2)
            area_a = torch.prod(bboxes_a[:, 2:], 1)
            area_b = torch.prod(bboxes_b[:, 2:], 1)
        en = (tl < br).to(tl.dtype).prod(dim=2)
        area_i = torch.prod(br - tl, 2) * en
        return area_i / (area_a[:, None] + area_b - area_i)

    _bx.bboxes_iou = bboxes_iou
    _yh.bboxes_iou = bboxes_iou

    # ...and once more in IOUloss.forward (the last `.type(t.type())` in the
    # training path; decode_outputs has one too but is inference-only).
    from yolox.models.losses import IOUloss

    def iou_forward(self, pred, target):
        assert pred.shape[0] == target.shape[0]
        pred = pred.view(-1, 4)
        target = target.view(-1, 4)
        tl = torch.max(pred[:, :2] - pred[:, 2:] / 2,
                       target[:, :2] - target[:, 2:] / 2)
        br = torch.min(pred[:, :2] + pred[:, 2:] / 2,
                       target[:, :2] + target[:, 2:] / 2)
        area_p = torch.prod(pred[:, 2:], 1)
        area_g = torch.prod(target[:, 2:], 1)
        en = (tl < br).to(tl.dtype).prod(dim=1)
        area_i = torch.prod(br - tl, 1) * en
        area_u = area_p + area_g - area_i
        iou = area_i / (area_u + 1e-16)
        if self.loss_type == "iou":
            loss = 1 - iou ** 2
        elif self.loss_type == "giou":
            c_tl = torch.min(pred[:, :2] - pred[:, 2:] / 2,
                             target[:, :2] - target[:, 2:] / 2)
            c_br = torch.max(pred[:, :2] + pred[:, 2:] / 2,
                             target[:, :2] + target[:, 2:] / 2)
            area_c = torch.prod(c_br - c_tl, 1)
            giou = iou - (area_c - area_u) / area_c.clamp(1e-16)
            loss = 1 - giou.clamp(min=-1.0, max=1.0)
        if self.reduction == "mean":
            loss = loss.mean()
        elif self.reduction == "sum":
            loss = loss.sum()
        return loss

    IOUloss.forward = iou_forward


def build_model(num_classes: int = 1):
    add_yolox_path()
    from yolox.exp.build import get_exp_by_name
    from yolox.models.network_blocks import BaseConv

    _patch_head_for_mps()
    exp = get_exp_by_name("yolox-nano")
    exp.num_classes = num_classes
    model = exp.get_model()
    stem_out = model.backbone.backbone.stem.conv.conv.out_channels
    model.backbone.backbone.stem = BaseConv(3, stem_out, ksize=3, stride=2,
                                            act="silu")
    return model


class RawExport(torch.nn.Module):
    """Raw per-level maps (1,H/s,W/s,4+1+C after NHWC), sigmoids baked in."""

    def __init__(self, model):
        super().__init__()
        self.backbone, self.head = model.backbone, model.head

    def forward(self, x):
        fpn = self.backbone(x)
        outs = []
        for k, (cls_conv, reg_conv, xk) in enumerate(
                zip(self.head.cls_convs, self.head.reg_convs, fpn)):
            xs = self.head.stems[k](xk)
            cls_out = self.head.cls_preds[k](cls_conv(xs))
            reg_feat = reg_conv(xs)
            reg_out = self.head.reg_preds[k](reg_feat)
            obj_out = self.head.obj_preds[k](reg_feat)
            outs.append(torch.cat(
                [reg_out, obj_out.sigmoid(), cls_out.sigmoid()], 1))
        return tuple(outs)


def decode_raw(outputs, strides=(8, 16, 32)):
    """Decode RawExport/eval maps -> (N, 5+C) [cx,cy,w,h,obj,cls...] px."""
    rows = []
    for out, stride in zip(outputs, strides):
        b, ch, h, w = out.shape
        ys, xs = torch.meshgrid(torch.arange(h), torch.arange(w),
                                indexing="ij")
        grid = torch.stack((xs, ys), 0).to(out.device, out.dtype)
        cxy = (out[:, :2] + grid) * stride
        wh = out[:, 2:4].exp() * stride
        rows.append(torch.cat([cxy, wh, out[:, 4:]], 1).flatten(2))
    return torch.cat(rows, 2).permute(0, 2, 1)  # (B, anchors, 5+C)
