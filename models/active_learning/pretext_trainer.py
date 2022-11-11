import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
import utils.logger as logging
from typing import List

import random

from datautils.path_loss import PathLoss
from datautils.target_dataset import get_target_pretrain_ds
from models.active_learning.pretext_dataloader import PretextDataLoader
from models.backbones.resnet import resnet_backbone
from models.self_sup.myow.trainer.myow_trainer import get_myow_trainer
from models.self_sup.simclr.trainer.simclr_trainer import SimCLRTrainer
from models.self_sup.simclr.trainer.simclr_trainer_v2 import SimCLRTrainerV2

from models.utils.commons import get_model_criterion
from models.utils.training_type_enum import TrainingType
from models.utils.ssl_method_enum import SSL_Method
from models.active_learning.al_method_enum import AL_Method
from utils.commons import load_path_loss, load_saved_state, save_path_loss, simple_load_model, simple_save_model

class PretextTrainer():
    def __init__(self, args, writer) -> None:
        self.args = args
        self.writer = writer
        self.criterion = None

    def train_proxy(self, samples, model, rebuild_al_model=False):

        # convert samples to loader
        loader = PretextDataLoader(self.args, samples).get_loader()
        logging.info("Beginning training the proxy")

        if self.args.method == SSL_Method.SIMCLR.value:
            trainer = SimCLRTrainer(
                args=self.args, writer=self.writer, encoder=model, dataloader=loader, 
                pretrain_level="1", rebuild_al_model=rebuild_al_model, 
                training_type=TrainingType.ACTIVE_LEARNING)

        elif self.args.method == SSL_Method.DCL.value:
            trainer = SimCLRTrainerV2(
                args=self.args, writer=self.writer, encoder=model, dataloader=loader, 
                pretrain_level="1", rebuild_al_model=rebuild_al_model, 
                training_type=TrainingType.ACTIVE_LEARNING)

        elif self.args.method == SSL_Method.MYOW.value:
            trainer = get_myow_trainer(
                args=self.args, writer=self.writer, encoder=model, dataloader=loader, 
                pretrain_level="1", rebuild_al_model=rebuild_al_model, 
                trainingType=TrainingType.ACTIVE_LEARNING)

        else:
            ValueError

        for epoch in range(self.args.al_epochs):
            logging.info('\nEpoch {}/{}'.format(epoch, self.args.al_epochs))
            logging.info('-' * 10)

            epoch_loss = trainer.train_epoch()

            # Decay Learning Rate
            trainer.scheduler.step()
            
            logging.info('Train Loss: {:.4f}'.format(epoch_loss))

        return trainer.model

    def finetune(self, model, samples: List[PathLoss]) -> List[PathLoss]:
        # Train using 70% of the samples with the highest loss. So this should be the source of the data
        loader = PretextDataLoader(self.args, samples, training_type=TrainingType.AL_FINETUNING).get_loader()

        logging.info("Generating the top1 scores")
        _preds = []
        model.eval()

        with torch.no_grad():
            for step, (image, _) in enumerate(loader):
                # images[0] = images[0].to(self.args.device)
                # images[1] = images[1].to(self.args.device)

                # _, _, outputs1, outputs2 = model(images[0], images[1])
                image = image.to(self.args.device)

                if self.args.method == SSL_Method.SIMCLR.value:
                    features = model(image)
                
                else:
                    features, _ = model(image)

                _preds.append(self.get_predictions(features))

                if step > 0 and step % 100 == 0:
                    logging.info(f"Step [{step}/{len(loader)}]")

        preds = torch.cat(_preds).numpy()
       
        return self.get_new_samples(preds, samples)


    def get_predictions(self, outputs):
        dist1 = F.softmax(outputs, dim=1)
        preds = dist1.detach().cpu()

        return preds

    def get_new_samples(self, preds, samples) -> List[PathLoss]:
        if self.args.al_method == AL_Method.LEAST_CONFIDENCE.value:
            probs = preds.max(axis=1)
            indices = probs.argsort(axis=0)

        elif self.args.al_method == AL_Method.ENTROPY.value:
            entropy = (np.log(preds) * preds).sum(axis=1) * -1.
            indices = entropy.argsort(axis=0)[::-1]

        elif self.args.al_method == AL_Method.BOTH.value:
            probs = preds.max(axis=1)
            indices1 = probs.argsort(axis=0)

            entropy = (np.log(preds) * preds).sum(axis=1) * -1.
            indices2 = entropy.argsort(axis=0)[::-1]

            indices = indices1 + indices2
            random.shuffle(indices)
            indices = indices[: (len(indices)/2)]

        else:
            raise ValueError(f"'{self.args.al_method}' method doesn't exist")

        new_samples = []
        for item in indices:
            new_samples.append(samples[item]) # Map back to original indices

        return new_samples[:5120]

    def make_batches(self, encoder) -> List[PathLoss]:
        # This is a hack to the model can use a batch size of 1 to compute the loss for all the samples
        batch_size = self.args.al_batch_size
        self.args.al_batch_size = 1

        model = encoder
        model, criterion = get_model_criterion(self.args, model, training_type=TrainingType.ACTIVE_LEARNING, is_make_batches=False)
        state = load_saved_state(self.args, pretrain_level="1")
        model.load_state_dict(state['model'], strict=False)

        model = model.to(self.args.device)
        loader = get_target_pretrain_ds(self.args, training_type=TrainingType.ACTIVE_LEARNING).get_loader()

        model.eval()
        pathloss = []

        logging.info("About to begin eval to make batches")
        count = 0
        with torch.no_grad():
            for step, (image, path) in enumerate(loader):
                image = image.to(self.args.device)

                # output = model(image)

                # # this needs to be really looked into
                # loss = criterion(output, output)

                # Forward pass to get output/logits
                feature1, output1 = model(image)
                feature2, output2 = model(image)

                # Calculate Loss: softmax --> cross entropy loss
                loss = criterion(output1, output2) + criterion(output2, output1)
                
                loss = loss.item()
                if step > 0 and step % 200 == 0:
                    logging.info(f"Step [{step}/{len(loader)}]\t Loss: {loss}")

                pathloss.append(PathLoss(path, loss))
                count +=1
        
        sorted_samples = sorted(pathloss, key=lambda x: x.loss, reverse=True)
        save_path_loss(self.args, self.args.al_path_loss_file, sorted_samples)

        self.args.al_batch_size = batch_size

        return sorted_samples

    def do_active_learning(self) -> List[PathLoss]:
        encoder = resnet_backbone(self.args.resnet, pretrained=False)
        proxy_model = encoder

        path_loss = load_path_loss(self.args, self.args.al_path_loss_file)
        if path_loss is None:
            path_loss = self.make_batches(encoder)

        pretraining_sample_pool = []
        rebuild_al_model = True

        for batch in range(0, self.args.al_batches): # change the '1' to '0'
            sample6400 = path_loss[batch * 6400 : (batch + 1) * 6400] # this should be changed to a size of 6000

            if batch > 0:
                logging.info('>> Getting previous checkpoint for batch ', batch + 1)
                proxy_model.load_state_dict(simple_load_model(self.args, f'proxy_{batch-1}.pth'), strict=False)

                # sampling
                samplek = self.finetune(proxy_model, sample6400)
            else:
                # first iteration: sample k at even intervals
                samplek = sample6400[:5120]

            pretraining_sample_pool.extend(samplek)

            if batch < self.args.al_batches - 1: # I want this not to happen for the last iteration since it would be needless
                proxy_model = self.train_proxy(
                    pretraining_sample_pool, 
                    proxy_model, rebuild_al_model=rebuild_al_model)

                rebuild_al_model=False
                simple_save_model(self.args, proxy_model, f'proxy_{batch}.pth')

        save_path_loss(self.args, self.args.pretrain_path_loss_file, pretraining_sample_pool)
        return pretraining_sample_pool

            
