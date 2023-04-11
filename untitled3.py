# -*- coding: utf-8 -*-
"""Untitled3.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1h7Vvmx9mIC7UJ7aA7MkG1FaHsnjrAgrw
"""

# Commented out IPython magic to ensure Python compatibility.
!pip install git+https://github.com/huawei-noah/Efficient-AI-Backbones.git
!git clone https://github.com/huawei-noah/Efficient-AI-Backbones.git
# %cd /content/Efficient-AI-Backbones/vig_pytorch

!pip install timm

import torch
import torchvision
import torchvision.transforms as transforms
import torch.nn as nn
import torch.nn.functional as F
import torchvision.utils
from torch.nn.parallel import DistributedDataParallel as NativeDDP
import math
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn import Sequential as Seq
from timm.models.layers import DropPath
from gcn_lib import Grapher, act_layer

def get_default_device():
    """Pick GPU if available, else CPU"""
    if torch.cuda.is_available():
        return torch.device('cuda')
    else:
        return torch.device('cpu')

device = get_default_device()

transform = transforms.Compose(
    [transforms.ToTensor(),
     transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))])

batch_size = 4

trainset = torchvision.datasets.CIFAR10(root='./data', train=True,
                                        download=True, transform=transform)
trainloader = torch.utils.data.DataLoader(trainset, batch_size=batch_size,
                                          shuffle=True, num_workers=2)

testset = torchvision.datasets.CIFAR10(root='./data', train=False,
                                       download=True, transform=transform)
testloader = torch.utils.data.DataLoader(testset, batch_size=batch_size,
                                         shuffle=False, num_workers=2)

classes = ('plane', 'car', 'bird', 'cat',
           'deer', 'dog', 'frog', 'horse', 'ship', 'truck')

class FFN(nn.Module):
    def __init__(self, in_features, hiddenp=None, outp=None, act='relu', drop_path=0.0):
        super().__init__()
        outp = outp or in_features
        hiddenp = hiddenp or in_features
        self.fc1 = nn.Sequential(
            nn.Conv2d(in_features, hiddenp, 1, stride=1, padding=0),
            nn.BatchNorm2d(hiddenp),
        )
        self.act = act_layer(act)
        self.fc2 = nn.Sequential(
            nn.Conv2d(hiddenp, outp, 1, stride=1, padding=0),
            nn.BatchNorm2d(outp),
        )
        self.identity = nn.Identity()

    def forward(self, x):
        shortcut = x
        x = self.fc1(x)
        x = self.act(x)
        x = self.fc2(x)
        x = self.identity(x) + shortcut
        return x


class Stem(nn.Module):
    def __init__(self, img_size=32, in_dim=3, out_dim=128, act='relu'):
        super().__init__()
        self.convs = nn.Sequential(
            nn.Conv2d(in_dim, out_dim//8, 3, stride=2, padding=2),
            nn.BatchNorm2d(out_dim//8),
            act_layer(act),
            nn.Conv2d(out_dim//8, out_dim//4, 3, stride=2, padding=2),
            nn.BatchNorm2d(out_dim//4),
            act_layer(act),
            nn.Conv2d(out_dim//4, out_dim//2, 3, stride=2, padding=2),
            nn.BatchNorm2d(out_dim//2),
            act_layer(act),
            nn.Conv2d(out_dim//2, out_dim, 3, stride=2, padding=2),
            nn.BatchNorm2d(out_dim),
            act_layer(act),
            nn.Conv2d(out_dim, out_dim, 3, stride=1, padding=2),
            nn.BatchNorm2d(out_dim),
        )
    def forward(self, x):
        x = self.convs(x)
        return x

channels = 128
        k = 4
        act = 'gelu'
        norm = 'batch'
        bias = False
        epsilon = 0.2
        stochastic = False
        conv = 'mr'
        n_blocks = 2
        drop_path = 0
        dropout = 0.2

class DeepGCN(torch.nn.Module):
    def __init__(self):
        super(DeepGCN, self).__init__()

        
        self.stem = Stem(out_dim=channels, act=act)

        num_knn = [int(x.item()) for x in torch.linspace(k, 2*k, n_blocks)]  # number of knn's k
        print('num_knn', num_knn)
        
        self.pos_embed = nn.Parameter(torch.zeros(4, channels, 6, 6))

        self.backbone = Seq(*[Seq(Grapher(channels, num_knn[i], 1, conv, act, norm,
                                                bias, stochastic, epsilon, 1, drop_path= 0),
                                      FFN(channels, channels * 4, act=act, drop_path=0)
                                     ) for i in range(n_blocks)])

        self.prediction = Seq(nn.Conv2d(channels, 256, 1, bias=True),
                              nn.BatchNorm2d(256),
                              act_layer(act),
                              nn.Dropout(dropout),
                              nn.Conv2d(256, 10, 1, bias=True))
        self.model_init()

    def model_init(self):
        for m in self.modules():
            if isinstance(m, torch.nn.Conv2d):
                torch.nn.init.kaiming_normal_(m.weight)
                m.weight.requires_grad = True
                if m.bias is not None:
                    m.bias.data.zero_()
                    m.bias.requires_grad = True

    def forward(self, inputs):
        x = self.stem(inputs) + self.pos_embed
        B, C, H, W = x.shape
        
        for i in range(n_blocks):
            x = self.backbone[i](x)

        x = F.adaptive_avg_pool2d(x, 1)
        return self.prediction(x).squeeze(-1).squeeze(-1)

net = DeepGCN()
net.to(device)

import torch.optim as optim

criterion = nn.CrossEntropyLoss()
optimizer = optim.SGD(net.parameters(), lr=0.001, momentum=0.9)

for epoch in range(30):  # loop over the dataset multiple times

    running_loss = 0.0
    for i, data in enumerate(trainloader, 0):
         # get the inputs; data is a list of [inputs, labels]
        inputs, labels = data[0].to(device), data[1].to(device)

        # zero the parameter gradients
        optimizer.zero_grad()

        # forward + backward + optimize
        outputs = net(inputs)
        loss = criterion(outputs, labels)
        loss.backward()
        optimizer.step()

        # print statistics
        running_loss += loss.item()
        if i % 12500 == 12499:    # print every 2000 mini-batches
            print(f'[{epoch + 1}, {i + 1:5d}] loss: {running_loss / 2000:.3f}')
            running_loss = 0.0

            correct = 0
            total = 0
            # since we're not training, we don't need to calculate the gradients for our outputs
            with torch.no_grad():
                for data in testloader:
                    images, labels = data
                    # calculate outputs by running images through the network
                    outputs = net(images)
                    # the class with the highest energy is what we choose as prediction
                    _, predicted = torch.max(outputs.data, 1)
                    total += labels.size(0)
                    correct += (predicted == labels).sum().item()

            print(f'Accuracy of the network on the 10000 test images: {100 * correct // total} %')
print('Finished Training')