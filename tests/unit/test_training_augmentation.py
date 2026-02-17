import pytest
from torchvision import transforms
from PIL import Image


class TestCreateTransforms:
    def test_train_transform_has_random_affine(self):
        from watermeter.training_core import create_transforms
        train_tf, _ = create_transforms(128)
        types = [type(t) for t in train_tf.transforms]
        assert transforms.RandomAffine in types

    def test_train_transform_has_random_perspective(self):
        from watermeter.training_core import create_transforms
        train_tf, _ = create_transforms(128)
        types = [type(t) for t in train_tf.transforms]
        assert transforms.RandomPerspective in types

    def test_train_transform_has_gaussian_blur(self):
        from watermeter.training_core import create_transforms
        train_tf, _ = create_transforms(128)
        types = [type(t) for t in train_tf.transforms]
        assert transforms.GaussianBlur in types

    def test_val_transform_has_no_augmentation(self):
        from watermeter.training_core import create_transforms
        _, val_tf = create_transforms(128)
        types = [type(t) for t in val_tf.transforms]
        assert transforms.RandomAffine not in types
        assert transforms.RandomPerspective not in types

    def test_train_transform_produces_tensor(self):
        from watermeter.training_core import create_transforms
        train_tf, _ = create_transforms(64)
        img = Image.new("RGB", (80, 80), "white")
        result = train_tf(img)
        assert result.shape == (3, 64, 64)

    def test_val_transform_produces_tensor(self):
        from watermeter.training_core import create_transforms
        _, val_tf = create_transforms(64)
        img = Image.new("RGB", (80, 80), "white")
        result = val_tf(img)
        assert result.shape == (3, 64, 64)
