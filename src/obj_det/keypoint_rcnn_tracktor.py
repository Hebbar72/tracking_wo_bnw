from collections import OrderedDict

import torch
import torch.nn.functional as F

from torchvision.models.detection import KeypointRCNN
from torchvision.models.detection.transform import resize_boxes, resize_keypoints
from torchvision.models.detection.roi_heads import keypointrcnn_inference


class KeypointRCNNTracktor(KeypointRCNN):

    def __init__(self, backbone, num_classes, **kwargs):
        # backbone = resnet_fpn_backbone('resnet50', False)
        super(KeypointRCNNTracktor, self).__init__(backbone, num_classes, **kwargs)

        # these values are cached to allow for feature reuse
        self.original_image_sizes = None
        self.preprocessed_images = None
        self.features = None

    def detect(self, img):
        device = list(self.parameters())[0].device
        img = img.to(device)

        detections = self(img)[0]

        return detections['boxes'].detach(), detections['scores'].detach()

    def predict_boxes(self, boxes_list):
        device = list(self.parameters())[0].device

        outputs = []
        for i, boxes in enumerate(boxes_list):
            boxes = resize_boxes(boxes, self.original_image_sizes[i], self.preprocessed_images.image_sizes[i])
            proposals = [boxes]

            features = OrderedDict([(k, feat[i:i+1]) for k, feat in self.features.items()])
            image_sizes = self.preprocessed_images.image_sizes[i:i+1]
            box_features = self.roi_heads.box_roi_pool(features, proposals, image_sizes)
            box_features = self.roi_heads.box_head(box_features)
            class_logits, box_regression = self.roi_heads.box_predictor(box_features)

            pred_boxes = self.roi_heads.box_coder.decode(box_regression, proposals)
            pred_scores = F.softmax(class_logits, -1)

            pred_boxes = pred_boxes[:, 1].detach()
            pred_scores = pred_scores[:, 1].detach()

            # GT boxes for masks
            pred_boxes = boxes
            pred_scores = torch.ones_like(pred_scores)

            pred_labels = torch.ones_like(pred_scores).long()

            if self.roi_heads.has_keypoint():
                keypoint_features = self.roi_heads.keypoint_roi_pool(features, [pred_boxes], image_sizes)
                keypoint_features = self.roi_heads.keypoint_head(keypoint_features)
                keypoint_logits = self.roi_heads.keypoint_predictor(keypoint_features)

                keypoints_probs, kp_scores = keypointrcnn_inference(keypoint_logits, [pred_boxes])
                keypoints_probs = keypoints_probs[0]
                kp_scores = kp_scores[0]

                # masks_probs = maskrcnn_inference(mask_logits, [pred_labels])[0]

            pred_boxes = resize_boxes(pred_boxes, self.preprocessed_images.image_sizes[i], self.original_image_sizes[i])
            output = {
                'boxes': pred_boxes,
                'scores': pred_scores,
                'labels': pred_labels}

            if self.roi_heads.has_keypoint():
                # masks = paste_masks_in_image(masks_probs, pred_boxes, self.original_image_sizes[i])
                keypoints = resize_keypoints(keypoints_probs, image_sizes[0], self.original_image_sizes[i])
                output['keypoints'] = keypoints
                output['keypoints_scores'] = kp_scores

            outputs.append(output)

        return outputs

    def load_image(self, images):
        self.original_image_sizes = [img.shape[-2:] for img in images]

        preprocessed_images, _ = self.transform(images, None)
        self.preprocessed_images = preprocessed_images

        self.features = self.backbone(preprocessed_images.tensors)
        if isinstance(self.features, torch.Tensor):
            self.features = OrderedDict([(0, self.features)])
