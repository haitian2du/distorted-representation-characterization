import torch
import torch.utils.data
from torch import optim

import argparse
import os
import time
import random
from tensorboardX import SummaryWriter

import models
import utils
import datasets
import d_ext_gradient_train


parser = argparse.ArgumentParser(description='Training a discriminator')
parser.add_argument('-e', '--epochs', default=200, type=int, metavar='N',
                    help='number of total epochs to run')
parser.add_argument('--start-epoch', default=0, type=int, metavar='N',
                    help='manual epoch number (useful on restarts)')
parser.add_argument('--print-freq', '-pf', default=10, type=int,
                    metavar='N', help='print frequency (default: 10)')
parser.add_argument('--write-freq', '-wf', default=5, type=int,
                    metavar='N', help='write frequency (default: 5)')
parser.add_argument('--write-enable', '-we', action='store_true', help='enable writing')
parser.add_argument('--dataset-dir',  default='./cure-tsr', type=str, help='directory for extracted gradients')


def main():
    global args
    args = parser.parse_args()

    vae_ckpt = './checkpoints/cure-tsr/vae/' \
               'vae_BCE_gradient_reducedCnnSeq-4layer_train-00_00_val-00_00/model_best.pth.tar'
    gradient_layer = 'down_6'  # kld
    gradient_layer2 = 'up_0'  # bce
    chall = '07_01'  # Training outlier class
    savedir = 'cure-tsr/d/%s/bce_kld_grad/d_BCE_ShallowLinear_norm_bce-%s_kld-%s_in-00_00_out-%s' \
              % (vae_ckpt.split('/')[-2], gradient_layer2, gradient_layer, chall)

    checkpointdir = os.path.join('./checkpoints', savedir)
    logdir = os.path.join('./logs', savedir)

    seed = random.randint(1, 100000)
    torch.manual_seed(seed)
    dataset_dir = os.path.join(args.dataset_dir, 'kld_grad/%s' % vae_ckpt.split('/')[-2])
    dataset_dir2 = os.path.join(args.dataset_dir, 'bce_grad/%s' % vae_ckpt.split('/')[-2])

    if args.write_enable:
        os.makedirs(checkpointdir)
        writer = SummaryWriter(log_dir=logdir)
        print('log directory: %s' % logdir)
        print('checkpoints directory: %s' % checkpointdir)

    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

    best_score = 1e20
    batch_size = 64

    vae = models.VAECURECNN()
    vae = torch.nn.DataParallel(vae).to(device)
    if os.path.isfile(vae_ckpt):
        print("=> loading checkpoint '{}'".format(vae_ckpt))
        checkpoint = torch.load(vae_ckpt)
        best_loss = checkpoint['best_loss']
        vae.load_state_dict(checkpoint['state_dict'])
        print("=> loaded checkpoint '{}' (epoch {}, best_loss {})"
              .format(vae_ckpt, checkpoint['epoch'], best_loss))
    else:
        print("=> no checkpoint found at '{}'".format(vae_ckpt))

    grad_dim = vae.module.down[6].weight.view(-1).shape[0] + vae.module.up[0].weight.view(-1).shape[0]

    d = models.DisShallowLinear(grad_dim)
    d = torch.nn.DataParallel(d).to(device)
    optimizer = optim.Adam(d.parameters(), lr=1e-3)

    in_train_loader = torch.utils.data.DataLoader(
        datasets.GradDataset([os.path.join(dataset_dir, '00_00_train_%s.pt' % gradient_layer),
                              os.path.join(dataset_dir2, '00_00_train_%s.pt' % gradient_layer2)]),
        batch_size=batch_size, shuffle=True)

    out_train_loader = torch.utils.data.DataLoader(
        datasets.GradDataset([os.path.join(dataset_dir, '%s_train_%s.pt' % (chall, gradient_layer)),
                              os.path.join(dataset_dir2, '%s_train_%s.pt' % (chall, gradient_layer2))]),
        batch_size=batch_size, shuffle=True)

    in_val_loader = torch.utils.data.DataLoader(
        datasets.GradDataset([os.path.join(dataset_dir, '00_00_val_%s.pt' % gradient_layer),
                              os.path.join(dataset_dir2, '00_00_val_%s.pt' % gradient_layer2)]),
        batch_size=batch_size, shuffle=True)

    out_val_loader = torch.utils.data.DataLoader(
        datasets.GradDataset([os.path.join(dataset_dir, '%s_val_%s.pt' % (chall, gradient_layer)),
                              os.path.join(dataset_dir2, '%s_val_%s.pt' % (chall, gradient_layer2))]),
        batch_size=batch_size, shuffle=True)

    # Start training
    timestart = time.time()
    for epoch in range(args.start_epoch, args.epochs):
        print('\n*** Start Training *** Epoch: [%d/%d]\n' % (epoch + 1, args.epochs))
        d_ext_gradient_train.train(d, None, device, in_train_loader, optimizer, epoch + 1, args.print_freq,
                                   out_iter=iter(out_train_loader))

        print('\n*** Start Testing *** Epoch: [%d/%d]\n' % (epoch + 1, args.epochs))
        loss, acc, _ = d_ext_gradient_train.test(d, None, device, in_val_loader, epoch + 1, args.print_freq,
                                                 out_iter=iter(out_val_loader))

        is_best = loss < best_score
        best_score = min(loss, best_score)

        if is_best:
            best_epoch = epoch + 1

        if args.write_enable:
            if epoch % args.write_freq == 0 or is_best is True:
                writer.add_scalar('loss', loss, epoch + 1)
                writer.add_scalar('accuracy', acc, epoch + 1)
                utils.save_checkpoint({
                    'epoch': epoch + 1,
                    'state_dict': d.state_dict(),
                    'best_acc': best_score,
                    'last_loss': loss,
                    'optimizer': optimizer.state_dict(),
                }, is_best, checkpointdir)

    if args.write_enable:
        writer.close()

    print('Best Testing Acc/Loss: %.3f at epoch %d' % (best_score, best_epoch))
    print('Best epoch: ', best_epoch)
    print('Total processing time: %.4f' % (time.time() - timestart))


if __name__ == '__main__':
    main()
