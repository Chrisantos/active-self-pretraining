import utils.logger as logging
from models.self_sup.simclr.loss.nt_xent_loss import NTXentLoss
from optim.optimizer import load_optimizer
from models.utils.commons import get_model_criterion, get_params
from models.utils.training_type_enum import TrainingType
from utils.commons import load_saved_state
from models.heads.nt_xent import NT_Xent

class SimCLRTrainer():
    def __init__(self, 
    args, writer, encoder, 
    dataloader, rebuild_al_model=False, 
    pretrain_level="1", training_type=TrainingType.BASE_PRETRAIN, 
    log_step=500) -> None:
    
        self.args = args
        self.writer = writer
        self.log_step = log_step

        self.train_loader = dataloader

        state = None
        if training_type == TrainingType.ACTIVE_LEARNING and not rebuild_al_model:
            self.model = encoder
            self.criterion = NTXentLoss(self.args)

        else:
            self.model, self.criterion = get_model_criterion(self.args, encoder, training_type)

            if training_type != TrainingType.BASE_PRETRAIN or self.args.epoch_num != self.args.base_epochs:
                state = load_saved_state(self.args, pretrain_level=pretrain_level)
                self.model.load_state_dict(state['model'], strict=False)

        self.model = self.model.to(self.args.device)

        self.train_params = get_params(self.args, training_type)
        self.optimizer, self.scheduler = load_optimizer(self.args, self.model.parameters(), state, self.train_params)

    def train_epoch(self) -> int:
        total_loss, total_num = 0.0, 0
        self.model.train()

        for step, (image, _) in enumerate(self.train_loader):
            # Clear gradients w.r.t. parameters
            self.optimizer.zero_grad()

            # image = image.to(self.args.device)
            output = self.model(image)
            loss = self.criterion(output)

            # Getting gradients w.r.t. parameters
            loss.backward()

            # Updating parameters
            self.optimizer.step()

            total_num += self.train_params.batch_size
            total_loss += loss.item() * self.train_params.batch_size

            if step % self.log_step == 0:
                logging.info(f"Step [{step}/{len(self.train_loader)}]\t Loss: {total_loss / total_num}")

            self.writer.add_scalar("Loss/train_epoch", loss, self.args.global_step)
            self.args.global_step += 1

        return total_loss / total_num