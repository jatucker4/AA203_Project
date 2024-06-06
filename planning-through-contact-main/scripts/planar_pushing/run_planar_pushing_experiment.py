from copy import copy
import logging
import os
from datetime import datetime
import sys
sys.path.append("/home/john/tpush/planning-through-contact-main")
import time

import hydra
from hydra.core.hydra_config import HydraConfig
from hydra.utils import get_original_cwd, instantiate
from omegaconf import OmegaConf, open_dict

from pydrake.all import (
    StartMeshcat,
)
from planning_through_contact.geometry.planar.non_collision import NonCollisionVariables

from planning_through_contact.geometry.planar.planar_pushing_trajectory import (
    PlanarPushingTrajectory,
)
from planning_through_contact.simulation.controllers.hybrid_mpc import HybridMpcConfig
from planning_through_contact.simulation.controllers.iiwa_hardware_station import (
    IiwaHardwareStation,
)
from planning_through_contact.simulation.controllers.mpc_position_source import (
    MPCPositionSource,
)
from planning_through_contact.simulation.environments.table_environment import (
    TableEnvironment,
)
from planning_through_contact.simulation.planar_pushing.planar_pushing_sim_config import (
    PlanarPushingSimConfig,
)
from planning_through_contact.simulation.sensors.optitrack_config import OptitrackConfig
from planning_through_contact.simulation.sensors.realsense_camera_config import (
    RealsenseCameraConfig,
)

logger = logging.getLogger(__name__)

state_estimator_meshcat = StartMeshcat()
station_meshcat = StartMeshcat()
# state_estimator_meshcat = None
station_meshcat = None
# logger.info(f"state estimator meshcat url {state_estimator_meshcat.web_url()}")

logging.basicConfig(level=logging.INFO)
logging.getLogger(
    "planning_through_contact.simulation.planar_pushing.pusher_pose_controller"
).setLevel(logging.DEBUG)
logging.getLogger(
    "planning_through_contact.simulation.controllers.hybrid_mpc"
).setLevel(logging.DEBUG)
logging.getLogger(
    "planning_through_contact.simulation.planar_pushing.iiwa_planner"
).setLevel(logging.DEBUG)
logging.getLogger(
    "planning_through_contact.simulation.environments.table_environment"
).setLevel(logging.DEBUG)


@hydra.main(version_base=None, config_path="/home/john/tpush/planning-through-contact-main/config", config_name="single_experiment.yaml")
def main(cfg: OmegaConf) -> None:
    now = datetime.now()
    now.strftime("%Y-%m-%d_%H-%M-%S")
    # Add log dir to config
    hydra_config = HydraConfig.get()
    full_log_dir = hydra_config.runtime.output_dir

    with open_dict(cfg):
        cfg.log_dir = os.path.relpath(full_log_dir, get_original_cwd() + "/outputs")

    logger.info(OmegaConf.to_yaml(cfg))

    traj_name = "traj_rounded"
    
    # plan_folder = "/home/john/tpush/hardware_demos/hw_demos_20240602000108_tee/hw_demo_2" # 0.5
    # plan_folder = "/home/john/tpush/hardware_demos/hw_demos_20240603153157_tee/hw_demo_2" # 0.8
    plan_folder = "/home/john/tpush/hardware_demos/hw_demos_20240605160152_tee/hw_demo_2" # 0.7
    # plan_folder = "/home/john/tpush/hardware_demos/hw_demos_20240605203608_tee/hw_demo_0/" # 0.3

    # traj_file = "/home/john/tpush/hardware_demos/hw_demos_20240602000108_tee/hw_demo_2/trajectory/traj_rounded.pkl" # 0.5
    # traj_file ="/home/john/tpush/hardware_demos/hw_demos_20240603153157_tee/hw_demo_2/trajectory/traj_rounded.pkl" # 0.8
    traj_file = "/home/john/tpush/hardware_demos/hw_demos_20240605160152_tee/hw_demo_2/trajectory/traj_rounded.pkl" # 0.7
    # traj_file = "/home/john/tpush/hardware_demos/hw_demos_20240605203608_tee/hw_demo_0/trajectory/traj_rounded.pkl" # 0.3

    # Copy plan folder to log dir
    os.system(f"cp -r {plan_folder} {full_log_dir}")

    # Set up config data structures
    try:
        traj = PlanarPushingTrajectory.load(traj_file)
    except FileNotFoundError:
        logger.error(f"Trajectory file {traj_file} not found")
        return

    # Non-collision time override
    # NEW_TIME = 4
    # new_config = traj.config
    # new_config.time_non_collision = NEW_TIME
    # new_knot_points = traj.path_knot_points
    # for knot_points in new_knot_points:
    #     if isinstance(knot_points, NonCollisionVariables):
    #         knot_points.time_in_mode = NEW_TIME

    # new_traj = PlanarPushingTrajectory(new_config, new_knot_points)
    # traj = new_traj

    mpc_config: HybridMpcConfig = instantiate(cfg.mpc_config)
    optitrack_config: OptitrackConfig = instantiate(cfg.optitrack_config)

    sim_config: PlanarPushingSimConfig = PlanarPushingSimConfig.from_traj(
        trajectory=traj, mpc_config=mpc_config, **cfg.sim_config
    )

    sim_config.use_hardware = False
    if state_estimator_meshcat is not None:
        state_estimator_meshcat.Delete()

    if sim_config.use_hardware:
        reset_experiment(
            sim_config, traj, station_meshcat, state_estimator_meshcat, optitrack_config
        )

    # Initialize position source
    position_source = MPCPositionSource(sim_config=sim_config, traj=traj)

    if sim_config.visualize_desired:
        station_meshcat_temp = station_meshcat
        state_estimator_meshcat_temp = state_estimator_meshcat
    else:
        station_meshcat_temp = None
        state_estimator_meshcat_temp = None
    # Initialize robot system
    position_controller = IiwaHardwareStation(
        sim_config=sim_config, meshcat=station_meshcat_temp
    )

    # Initialize environment
    environment = TableEnvironment(
        desired_position_source=position_source,
        robot_system=position_controller,
        sim_config=sim_config,
        optitrack_config=optitrack_config,
        station_meshcat=station_meshcat_temp,
        state_estimator_meshcat=state_estimator_meshcat_temp,
    )

    try:
        if sim_config.use_hardware and cfg.realsense_config.should_record:
            from planning_through_contact.simulation.sensors.realsense import (
                RealsenseCamera,
            )

            # Initialize cameras
            camera_config: RealsenseCameraConfig = instantiate(
                cfg.realsense_config.realsense_camera_config
            )
            camera_config.output_dir = full_log_dir
            camera1 = RealsenseCamera(
                name=cfg.realsense_config.camera1_name,
                serial_number=cfg.realsense_config.camera1_serial_number,
                config=camera_config,
            )
            camera2 = RealsenseCamera(
                name=cfg.realsense_config.camera2_name,
                serial_number=cfg.realsense_config.camera2_serial_number,
                config=camera_config,
            )
            camera3 = RealsenseCamera(
                name=cfg.realsense_config.camera3_name,
                serial_number=cfg.realsense_config.camera3_serial_number,
                config=camera_config,
            )
            camera1.start_recording()
            camera2.start_recording()
            camera3.start_recording()

        recording_name = os.path.join(
            full_log_dir,
            traj_name
            + f"_hw_{sim_config.use_hardware}_cl{sim_config.closed_loop}"
            + ".html"
            if cfg.save_experiment_data
            else None,
        )
        timeout = (
            cfg.override_duration
            if "override_duration" in cfg
            else traj.end_time + sim_config.delay_before_execution
        )
        start = time.time()
        # Run simulation
        environment.simulate(
            timeout=timeout,
            recording_file=recording_name,
            save_dir=full_log_dir,
        )
        end = time.time()
        print("Time to complete sim: ")
        print(end-start)

        if sim_config.use_hardware and cfg.realsense_config.should_record:
            camera1.stop_recording()
            camera2.stop_recording()
            camera3.stop_recording()
    except KeyboardInterrupt:
        environment.save_logs(recording_name, full_log_dir)
        if sim_config.use_hardware and cfg.realsense_config.should_record:
            camera1.stop_recording()
            camera2.stop_recording()
            camera3.stop_recording()


def reset_experiment(
    sim_config: PlanarPushingSimConfig,
    traj,
    station_meshcat,
    state_estimator_meshcat,
    optitrack_config,
):
    reset_sim_config = copy(sim_config)
    reset_sim_config.delay_before_execution = 600
    # Initialize position source
    position_source = MPCPositionSource(sim_config=reset_sim_config, traj=traj)

    # Initialize robot system
    position_controller = IiwaHardwareStation(
        sim_config=reset_sim_config, meshcat=station_meshcat
    )

    # Initialize environment
    environment = TableEnvironment(
        desired_position_source=position_source,
        robot_system=position_controller,
        sim_config=reset_sim_config,
        optitrack_config=optitrack_config,
        station_meshcat=station_meshcat,
        state_estimator_meshcat=state_estimator_meshcat,
    )

    start = time.time()
    environment.simulate(
        timeout=reset_sim_config.delay_before_execution,
        for_reset=True,
    )
    end = time.time()
    print(end - start)

    station_meshcat.Delete()
    state_estimator_meshcat.Delete()


if __name__ == "__main__":
    main()
