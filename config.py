import functools
import logging
import os
import shutil
import sys
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, ClassVar, Generic, List, Optional, Tuple, TypeVar

import numpy as np
import torch
import tqdm
import wandb
from simple_parsing import field, mutable_field
from torch import Tensor, nn, optim
from torch.nn import functional as F
from torch.utils.data import DataLoader
from torchvision import datasets, transforms
from torchvision.utils import save_image
from utils.json_utils import JsonSerializable
from utils import cuda_available, gpus_available, set_seed

import logging
logging.basicConfig(
    format='%(asctime)s,%(msecs)d %(levelname)-8s [./%(filename)s:%(lineno)d] %(message)s',
    datefmt='%Y-%m-%d:%H:%M:%S',
    level=logging.INFO,
)
logging.getLogger('simple_parsing').addHandler(logging.NullHandler())

logger = logging.getLogger(__file__)

@dataclass
class Config:
    """Settings related to the training setup. """

    debug: bool = field(alias="-d", default=False, action="store_true", nargs=0)      # enable debug mode.
    verbose: bool = field(alias="-v", default=False, action="store_true", nargs=0)    # enable verbose mode.

    # Number of steps to perform instead of complete epochs when debugging
    debug_steps: Optional[int] = None
    data_dir: Path = Path("data")  # data directory.

    log_dir_root: Path = Path("results") # Logging directory.
    log_interval: int = 10   # How many batches to wait between logging calls.
    
    random_seed: int = 1            # Random seed.
    use_cuda: bool = cuda_available # Whether or not to use CUDA.
    
    # num_workers for the dataloaders.
    num_workers: int = 0

    # Which specific device to use.
    # NOTE: Can be set directly with the command-line! (ex: "--device cuda")
    device: torch.device = torch.device("cuda" if cuda_available else "cpu")
    
    use_wandb: bool = True # Whether or not to log results to wandb
    # Name used to easily group runs together.
    # Used to create a parent folder that will contain the `run_name` directory. 
    run_group: Optional[str] = None 
    run_name: Optional[str] = None  # Wandb run name. If None, will use wandb's automatic name generation
    
    # An run number is used to differentiate different iterations of the same experiment.
    # Runs with the same name can be later grouped with wandb to produce stderr plots.
    run_number: Optional[int] = None 
    
    # Save the command-line arguments that were used to create this run.
    argv: List[str] = field(init=False, default_factory=sys.argv.copy)

    # Early stopping patience: number of validation epochs with increasing loss
    # to wait for before stopping training.
    # TODO: use an actual validation set instead of the test set for validation.
    patience: int = 3

    if 'WANDB_DIR' in os.environ:
        wandb_path=Path(os.environ['WANDB_DIR'])
    else:
        wandb_path = './results'

    def __post_init__(self):
        # set the manual seed (for reproducibility)
        set_seed(self.random_seed + (self.run_number or 0))
        
        if self.use_cuda and not cuda_available:
            print("Cannot use the passed value of argument 'use_cuda', as CUDA "
                  "is not available!")
            self.use_cuda = False
        if not self.use_cuda:
            self.device = torch.device("cpu")
        
        if self.debug:
            self.use_wandb = False
            if self.run_name is None:
                self.run_name = "debug"
            
            # if self.log_dir.exists():
            #     # wipe out the debug folder every time.
            #     shutil.rmtree(self.log_dir)
            #     if self.log_dir.exists():
            #         # wipe out the debug folder every time.
            #         shutil.rmtree(self.log_dir)
            #         print(f"REMOVED THE LOG DIR {self.log_dir}")
            
            # self.log_dir.mkdir(exist_ok=False, parents=True)

            if self.use_cuda:
                # TODO: set CUDA deterministic.
                torch.backends.cudnn.deterministic = True
                torch.backends.cudnn.benchmark = False

    @property
    def log_dir(self):
        return self.log_dir_root.joinpath(
            (self.run_group or ""),
            (self.run_name or 'default'),
            (f"run_{self.run_number}" if self.run_number is not None else ""),
        )

    def get_logger(self, name: str) -> logging.Logger:
        """ TODO: figure out if we should add handlers, etc. """
        logger = logging.getLogger(name)
        return logger

    def wandb_init(self, experiment):    
        if self.run_name is None:
            # TODO: Create a run name using the coefficients of the tasks, etc?
            # At the moment, if no run name is given, the 'random' name from wandb is used.
            pass

        config_dict = experiment.to_config_dict()
        self.run_group = self.run_group or type(experiment).__name__
        # store this id to use it later when resuming
        # run_id = wandb.util.generate_id()
        # logger.info(f"Wandb run id: {run_id}")
        run = wandb.init(
            project='SSCL',
            name=self.run_name,
            # id=run_id,
            group=self.run_group,
            config=config_dict,
            dir=str(self.wandb_path),
            notes=experiment.notes,
            reinit=True,
            resume="allow",
        )
        wandb.run.save()

        if self.run_name is None:
            self.run_name = wandb.run.name
        
        print(f"Using wandb. Group name: {self.run_group} run name: {self.run_name}, log_dir: {self.log_dir}")
        return run

# shared config object.
## TODO: unused, but might be useful!
# config: Config = Config()

class TqdmLoggingHandler(logging.Handler):
    def __init__(self, level=logging.NOTSET):
        super().__init__(level)

    def emit(self, record):
        try:
            msg = self.format(record)
            tqdm.tqdm.write(msg)
            self.flush()
        except (KeyboardInterrupt, SystemExit):
            raise
        except:
            self.handleError(record)  
