'''
Adapted with modifications from

@article{reed2021self,
  title={Self-supervised pretraining improves self-supervised pretraining.},
  author={Reed, Colorado J and Yue, Xiangyu and Nrusimha, Ani and Ebrahimi, Sayna and Vijaykumar, Vivek and Mao, Richard and Li, Bo and Zhang, Shanghang and Guillory, Devin and Metzger, Sean and Keutzer, Kurt and Darrell, Trevor},
  journal={arXiv preprint arXiv:2103.12718},
  year={2021}
}

'''


import torch
import torch.nn as nn

from utils.commons import accuracy

class LogLossHead(nn.Module):
    """Simplest classifier head, with only one fc layer.
    """

    def __init__(self,
        encoder,
        with_avg_pool=False,
        in_channels=2048,
        num_classes=1000):

        super(LogLossHead, self).__init__()

        self.encoder = encoder
        self.with_avg_pool = with_avg_pool
        self.in_channels = in_channels
        self.num_classes = num_classes

        self.criterion = nn.CrossEntropyLoss()
            
        if self.with_avg_pool:
            self.avg_pool = nn.AdaptiveAvgPool2d((1, 1))


        # self.fc_cls = nn.Sequential([self.encoder.fc, nn.Linear(in_channels, num_classes)]) # not sure of this

        self.encoder.fc = nn.Linear(in_channels, num_classes)
        self.fc_cls = self.encoder

    # def init_weights(self, init_linear='normal', std=0.01, bias=0.):
    #     assert init_linear in ['normal', 'kaiming'], \
    #         "Undefined init_linear: {}".format(init_linear)
    #     for m in self.modules():
    #         if isinstance(m, nn.Linear):
    #             if init_linear == 'normal':
    #                 normal_init(m, std=std, bias=bias)
    #             else:
    #                 kaiming_init(m, mode='fan_in', nonlinearity='relu')
    #         elif isinstance(m,
    #                         (nn.BatchNorm2d, nn.GroupNorm, nn.SyncBatchNorm)):
    #             if m.weight is not None:
    #                 nn.init.constant_(m.weight, 1)
    #             if m.bias is not None:
    #                 nn.init.constant_(m.bias, 0)

    # def forward(self, x):
    #     assert isinstance(x, (tuple, list)) and len(x) == 1
    #     x = x[0]
    #     if self.with_avg_pool:
    #         assert x.dim() == 4, \
    #             "Tensor must has 4 dims, got: {}".format(x.dim())
    #         x = self.avg_pool(x)

    #     x = x.view(x.size(0), -1)
    #     cls_score = self.fc_cls(x)

    #     return [cls_score]

    def forward(self, x):
        assert isinstance(x, (tuple, list)) and len(x) == 1
        x = x[0]
        if self.with_avg_pool:
            assert x.dim() == 4, \
                "Tensor must has 4 dims, got: {}".format(x.dim())
            x = self.avg_pool(x)

        x = x.view(x.size(0), -1) # this flattens the input before setting it in the FC layer
        cls_score = self.fc_cls(x)

        return cls_score
        
    def loss(self, cls_score, labels):
        losses = dict()
        assert isinstance(cls_score, (tuple, list)) and len(cls_score) == 1

        losses['loss'] = self.criterion(cls_score[0], labels)
        losses['acc'] = accuracy(cls_score[0], labels)
            
        return losses
