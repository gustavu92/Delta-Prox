from dprox import *
from dprox.utils import *
from dprox.contrib.derain import LearnableDegOp

def test():
    b = hf.load_image('examples/derain/derain_input.png')
    gt = hf.load_image('examples/derain/derain_target.png')
    imshow(gt, b)

    # custom linop
    A = LearnableDegOp().cuda()
    def forward_fn(input, step): return A.forward(input, step)
    def adjoint_fn(input, step): return A.adjoint(input, step)
    raining = LinOpFactory(forward_fn, adjoint_fn)

    # build solver
    x = Variable()
    data_term = sum_squares(raining(x), b)
    reg_term = unrolled_prior(x)
    obj = data_term + reg_term
    solver = compile(obj, method='pgd')

    # load parameters
    ckpt = hf.load_checkpoint('image_deraining/derain_pdg.pth')
    A.load_state_dict(ckpt['linop'])
    reg_term.load_state_dict(ckpt['prior'])
    rhos = ckpt['rhos']

    out = solver.solve(x0=b, rhos=rhos, max_iter=7)
    out = to_ndarray(out, debatch=True) + b
    print(psnr(out, gt))  # 35.92
    imshow(gt, out)
    assert psnr(out, gt) - 35.92 < 0.1
