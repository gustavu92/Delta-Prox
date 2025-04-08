import torch
from .common import *


class HeightMap(nn.Module):
    def __init__(self, zernike_volume, initial_zernike_coefs, height_map_shape, wave_lengths, refractive_idcs, xx, yy, sensor_distance):
        super().__init__()
        self.zernike_volume = zernike_volume
        self.initial_zernike_coefs = initial_zernike_coefs
        self.wave_lengths = wave_lengths
        self.refractive_idcs = refractive_idcs
        delta_N = refractive_idcs.view([1, -1, 1, 1]) - 1.
        # wave number
        wave_nos = 2. * torch.pi / wave_lengths
        wave_nos = wave_nos.view([1, -1, 1, 1])
        self.register_buffer('delta_N', delta_N, persistent=False)
        self.register_buffer('wave_nos', wave_nos, persistent=False)
        self.xx = xx
        self.yy = yy
        self.sensor_distance = sensor_distance

        if self.zernike_volume is None:
            height_map_sqrt = self._fresnel_phase_init(1)
            self.height_map_sqrt = nn.Parameter(height_map_sqrt)
        else:
            num_zernike_coeffs = zernike_volume.shape[0]
            self.zernike_coeffs = nn.ParameterList()

            for k in range(num_zernike_coeffs):
                value = initial_zernike_coefs.get(k, 0.0)
                self.zernike_coeffs.append(nn.Parameter(torch.tensor(value)))

    def _fresnel_phase_init(self, idx=1):
        """
        calculate the Fresnel lens phase and convert it to a height map.

        :param idx: idx is an optional parameter that specifies the index of the wavelength to use in
        the calculation. It is set to 1 by default, defaults to 1 (optional)
        :return: the square root of the height map calculated from the Fresnel phase.
        """
        k = 2 * torch.pi / self.wave_lengths[idx]
        fresnel_phase = - k * ((self.xx**2 + self.yy**2)
                               [None][None] / (2 * self.sensor_distance))
        fresnel_phase = fresnel_phase % (torch.pi * 2)
        height_map = self.phase_to_height_map(fresnel_phase, idx)
        return height_map ** 0.5

    def get_phase_profile(self, height_map=None):
        """
        calculate the phase profile of a height map using wave numbers and phase delay.

        :param height_map: A 2D tensor representing the height map of the surface. It is used to
        calculate the phase delay induced by the height field
        :return: a complex exponential phase profile calculated from the input height map.
        """
        if self.zernike_volume is None:
            height_map = torch.square(self.height_map_sqrt)
            phi = self.wave_nos * self.delta_N * height_map
        else:
            phi = 2 * np.pi * torch.sum(torch.stack([coef * self.zernike_volume[k] for k, coef in enumerate(self.zernike_coeffs)]), dim=0, keepdim=True).unsqueeze(0)

        return torch.exp(1j * phi)
    
    def height_from_zernike(self,):
        phi = 2 * np.pi * torch.sum(torch.stack([coef * self.zernike_volume[k] for k, coef in enumerate(self.zernike_coeffs)]), dim=0, keepdim=True).unsqueeze(0)
        height_map = (self.wave_lengths[0] * phi) / (2 * np.pi * (self.refractive_idcs[0] - 1))
        return height_map

    def phase_to_height_map(self, phi, wave_length_idx=1):
        """
        take in a phase map and return a corresponding height map using the given wave
        length and refractive index.

        :param phi: The phase profile of the height map at a specific wavelength associated with `wave_length_idx`
        :param wave_length_idx: The index of the wavelength in the list of available wavelengths. This
        is used to retrieve the corresponding `delta_N` value for the given wavelength, defaults to 1
        (optional)
        :return: a height map calculated from the input phase and other parameters such as wave length
        and delta N.
        """
        wave_length = self.wave_lengths[wave_length_idx]
        delta_n = self.delta_N.view(-1)[wave_length_idx]
        k = 2. * torch.pi / wave_length
        phi = phi % (2 * torch.pi)
        height_map = phi / k / delta_n
        return height_map


class RGBCollimator(nn.Module):
    def __init__(self,
                 zernike_volume,
                 initial_zernike_coefs,
                 sensor_distance,
                 refractive_idcs,
                 wave_lengths,
                 patch_size,
                 sample_interval,
                 wave_resolution,
                 ):
        super().__init__()
        self.zernike_volume = zernike_volume
        self.initial_zernike_coefs = initial_zernike_coefs
        self.wave_res = wave_resolution
        self.wave_lengths = wave_lengths
        self.sensor_distance = sensor_distance
        self.sample_interval = sample_interval
        self.patch_size = patch_size
        self.refractive_idcs = refractive_idcs
        self._init_setup()

    def get_psf(self, phase_profile=None):
        """
        calculate the point spread function (PSF) of an optical system given a phase
        profile and other parameters.

        :param phase_profile: A 2D tensor representing the phase profile of the optical system. It is
        multiplied element-wise with the input field before propagation
        :return: a PSF (Point Spread Function) which is a 2D tensor representing the intensity
        distribution of the image formed by a point source after passing through the optical system.
        """
        if phase_profile is None:
            phase_profile = self.height_map.get_phase_profile()
        field = phase_profile * self.input_field
        field = self.aperture * field
        field = self.propagator(field)
        psfs = (torch.abs(field) ** 2).float()
        psfs = area_downsampling(psfs, self.patch_size)
        # psfs = psfs / psfs.sum(dim=[2, 3], keepdim=True)
        psfs = psfs / psfs.sum()
        return psfs

    def forward(self, input_img, phase_profile=None, circular=False):
        psfs = self.get_psf(phase_profile)
        output_image = img_psf_conv(input_img, psfs, circular=circular)
        return output_image, psfs

    def _init_setup(self):
        input_field = torch.ones(
            (1, len(self.wave_lengths), self.wave_res[0], self.wave_res[1]))
        self.register_buffer("input_field", input_field, persistent=False)

        xx, yy = get_coordinate(self.wave_res[0], self.wave_res[1],
                                self.sample_interval, self.sample_interval)
        self.register_buffer("xx", xx, persistent=False)
        self.register_buffer("yy", yy, persistent=False)

        aperture = self._get_circular_aperture(xx, yy)
        self.register_buffer("aperture", aperture, persistent=False)

        self.height_map = self._get_height_map()
        self.propagator = self._get_propagator()

    def _get_height_map(self):
        height_map_shape = (1, 3, self.wave_res[0], self.wave_res[1])
        height_map = HeightMap(self.zernike_volume,
                               self.initial_zernike_coefs,
                               height_map_shape,
                               self.wave_lengths,
                               self.refractive_idcs,
                               self.xx, self.yy,
                               self.sensor_distance)
        return height_map

    def _get_propagator(self):
        input_shape = (1, 3, self.wave_res[0], self.wave_res[1])
        propagator = FresnelPropagator(input_shape,
                                       self.sensor_distance,
                                       self.sample_interval,
                                       self.wave_lengths)
        return propagator

    def _get_circular_aperture(self, xx, yy):
        max_val = xx.max()
        r = torch.sqrt(xx ** 2 + yy ** 2)
        aperture = (r < max_val).float()[None][None]
        return aperture


@dataclass
class DOEModelConfig:
    zernike_volume: torch.Tensor = None
    initial_zernike_coefs: dict = None
    circular: bool = True  # circular convolution
    sensor_distance: float = 15e-3  # Distance of sensor to aperture
    refractive_idcs: torch.Tensor = field(default_factory=lambda: torch.tensor(
        [1.4648, 1.4599, 1.4568]))  # Refractive indices of the phase plate
    wave_lengths: torch.Tensor = field(default_factory=lambda: torch.tensor(
        [460, 550, 640]) * 1e-9)  # Wavelengths to be modeled and optimized for
    num_steps: int = 10001  # Number of SGD steps
    # Size of patches to be extracted from images, and resolution of simulated sensor
    patch_size: int = 748
    # Sampling interval (size of one "pixel" in the simulated wavefront)
    sample_interval: float = 2e-6
    # Resolution of the simulated wavefront
    wave_resolution: tuple = (1496, 1496)
    # Additional model parameters
    model_kwargs: dict = field(default_factory=dict)


def build_doe_model(config: DOEModelConfig = DOEModelConfig()):
    """
    build a DOE (Diffractive Optical Element) model using the input configuration.

    :param config: DOEModelConfig object that contains the following parameters:
    :type config: DOEModelConfig
    :return: The function `build_doe_model` is returning an instance of the `RGBCollimator` class, which
    is initialized with the parameters passed in the `DOEModelConfig` object `config`.
    """
    rgb_collim_model = RGBCollimator(config.zernike_volume,
                                     config.initial_zernike_coefs,
                                     config.sensor_distance,
                                     refractive_idcs=config.refractive_idcs,
                                     wave_lengths=config.wave_lengths,
                                     patch_size=config.patch_size,
                                     sample_interval=config.sample_interval,
                                     wave_resolution=config.wave_resolution,
                                     )
    return rgb_collim_model


def build_baseline_profile(rgb_collim_model: RGBCollimator):
    """
    build a baseline profile for a given RGB collimator model by calculating the Fresnel
    phase and height map.

    :param rgb_collim_model: An instance of the RGBCollimator class, which likely represents a system
    for collimating light of different wavelengths (red, green, and blue) onto a sensor or detector
    :type rgb_collim_model: RGBCollimator
    :return: the fresnel phase profile of the collimator model after applying the fresnel phase shift
    due to propagation to a sensor distance. The fresnel phase profile is calculated using the height
    map obtained from the phase-to-height map conversion function.
    """
    if rgb_collim_model.zernike_volume is None:
        k = 2 * torch.pi / rgb_collim_model.wave_lengths[1]
        fresnel_phase = - k * ((rgb_collim_model.xx**2 + rgb_collim_model.yy**2)
                               [None][None] / (2 * rgb_collim_model.sensor_distance))
        fresnel_phase = fresnel_phase % (torch.pi * 2)
        height_map = rgb_collim_model.height_map.phase_to_height_map(
            fresnel_phase, 1)
    else:
        pass
        # TODO

    fresnel_phase_c = rgb_collim_model.height_map.get_phase_profile(height_map)
    return fresnel_phase_c
