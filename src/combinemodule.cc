/*
 * Copyright 2008-2011 Sergio Pascual
 *
 * This file is part of PyEmir
 *
 * PyEmir is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * PyEmir is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with PyEmir.  If not, see <http://www.gnu.org/licenses/>.
 *
 */


#include <vector>
#include <memory>
#include <algorithm>

#include <Python.h>

#define PY_ARRAY_UNIQUE_SYMBOL numina_ARRAY_API
#include <numpy/arrayobject.h>

#include "nu_combine_methods.h"
#include "nu_combine.h"

typedef std::vector<PyArrayIterObject*> VectorPyArrayIter;

PyDoc_STRVAR(combine__doc__, "Internal combine module, not to be used directly.");

// Convenience function to avoid the Py_DECREF macro
static inline void My_Py_Decref(PyObject* obj)
{
  Py_DECREF(obj);
}

static inline void My_PyArray_Iter_Decref(PyArrayIterObject* it)
{
  Py_DECREF(it);
}

// Convenience function to avoid the PyArray_ITER_NEXT macro
static inline void My_PyArray_Iter_Next(PyArrayIterObject* it)
{
  PyArray_ITER_NEXT(it);
}

// Convenience PyArrayIterObject* creator
static inline PyArrayIterObject* My_PyArray_IterNew(PyObject* obj)
{
  return (PyArrayIterObject*) PyArray_IterNew(obj);
}

// An exception in this module
static PyObject* CombineError;

// Convenience check function
static inline bool check_1d_array(PyObject* array, size_t nimages, const char* name) {
  if (PyArray_NDIM(array) != 1)
  {
    PyErr_Format(CombineError, "%s dimension %i != 1", name, PyArray_NDIM(array));
    return false;
  }
  if (PyArray_SIZE(array) != nimages)
  {
    PyErr_Format(CombineError, "%s size %zd != number of images", name, PyArray_SIZE(array));
    return false;
  }
  return true;
}

static PyObject* py_generic_combine(PyObject *self, PyObject *args)
{
  /* Arguments */
  PyObject *images = NULL;
  PyObject *masks = NULL;
  // Output has one dimension more than the inputs, of size
  // OUTDIM
  const size_t OUTDIM = NU_COMBINE_OUTDIM;
  PyObject *out[OUTDIM] = {NULL, NULL, NULL};
  PyObject* fnc = NULL;

  PyObject* scales = NULL;
  PyObject* zeros = NULL;
  PyObject* weights = NULL;


  PyObject *images_seq = NULL;
  PyObject *masks_seq = NULL;
  PyObject* zeros_arr = NULL;
  PyObject* scales_arr = NULL;
  PyObject* weights_arr = NULL;

  void *func = (void*)NU_mean_function;
  void *data = NULL;

  Py_ssize_t nimages = 0;
  Py_ssize_t nmasks = 0;
  Py_ssize_t ui = 0;

  PyObject** allimages = NULL;
  PyObject** allmasks = NULL;

  double* zbuffer = NULL;
  double* sbuffer = NULL;
  double* wbuffer = NULL;

  int ok = PyArg_ParseTuple(args,
      "OOO!O!O!|OOOO:generic_combine",
      &fnc,
      &images,
      &PyArray_Type, &out[0],
      &PyArray_Type, &out[1],
      &PyArray_Type, &out[2],
      &masks,
      &zeros,
      &scales,
      &weights);

  if (!ok)
  {
    goto exit;
  }

  images_seq = PySequence_Fast(images, "expected a sequence");
  nimages = PySequence_Size(images_seq);

  if (nimages == 0) {
    PyErr_Format(CombineError, "data list is empty");
    goto exit;
  }

  // Converted to an array of pointers
  allimages = PySequence_Fast_ITEMS(images_seq);

  // Checking for images
  for(ui = 0; ui < nimages; ++ui) {
    if (not NU_combine_image_check(CombineError, allimages[ui], allimages[0], allimages[0], "data", ui))
      goto exit;
  }

  // Checking for outputs
  for(ui = 0; ui < OUTDIM; ++ui) {
    if (not NU_combine_image_check(CombineError, out[ui], allimages[0], out[0], "output", ui))
      goto exit;
  }

  if (PyCObject_Check(fnc)) {
      func = PyCObject_AsVoidPtr(fnc);
      data = PyCObject_GetDesc(fnc);
  } else {
      PyErr_SetString(PyExc_RuntimeError,
                                      "function parameter is not callable");
      goto exit;
  }

  // Checking zeros, scales and weights
  if (zeros == Py_None) {
    zbuffer = new double[nimages];
    std::fill(zbuffer, zbuffer + nimages, 0.0);
  }
  else {
    zeros_arr = PyArray_FROM_OTF(zeros, NPY_DOUBLE, NPY_IN_ARRAY);
    if (not check_1d_array(zeros, nimages, "zeros"))
      goto exit;

    zbuffer = (double*)PyArray_DATA(zeros_arr);
  }

  if (scales == Py_None) {
    sbuffer = new double[nimages];
    std::fill(sbuffer, sbuffer + nimages, 1.0);
  }
  else {
    scales_arr = PyArray_FROM_OTF(scales, NPY_DOUBLE, NPY_IN_ARRAY);
    if (not check_1d_array(scales_arr, nimages, "scales"))
      goto exit;

    sbuffer = (double*)PyArray_DATA(scales_arr);
  }

  if (weights == Py_None) {
    wbuffer = new double[nimages];
    std::fill(wbuffer, wbuffer + nimages, 1.0);
  }
  else {
    weights_arr = PyArray_FROM_OTF(weights, NPY_DOUBLE, NPY_IN_ARRAY);
    if (not check_1d_array(weights, nimages, "weights"))
      goto exit;

    wbuffer = (double*)PyArray_DATA(weights_arr);
  }

  if (masks == Py_None) {
    allmasks = NULL;
  }
  else {
    // Checking the masks
    masks_seq = PySequence_Fast(masks, "expected a sequence");
    nmasks = PySequence_Size(masks_seq);

    if (nimages != nmasks) {
      PyErr_Format(CombineError, "number of images (%zd) and masks (%zd) is different", nimages, nmasks);
      goto exit;
    }

    allmasks = PySequence_Fast_ITEMS(masks_seq);

    for(ui = 0; ui < nimages; ++ui) {
      if (not NU_combine_image_check(CombineError, allmasks[ui], allimages[0], allmasks[0], "masks", ui))
        goto exit;
    }
  }

  if( not NU_generic_combine(allimages, allmasks, nimages, out,
      (CombineFunc)func, data, zbuffer, sbuffer, wbuffer)
    )
    goto exit;

exit:
  Py_XDECREF(images_seq);

  if (masks != Py_None)
    Py_XDECREF(masks_seq);

  if (zeros == Py_None)
    delete [] zbuffer;

  Py_XDECREF(zeros_arr);

  if (scales == Py_None)
    delete [] sbuffer;

  Py_XDECREF(scales_arr);

  if (weights == Py_None)
    delete [] wbuffer;

  Py_XDECREF(weights_arr);
  return PyErr_Occurred() ? NULL : Py_BuildValue("");

}

static PyObject *
py_method_mean(PyObject *obj, PyObject *args) {
  if (not PyArg_ParseTuple(args, "")) {
    PyErr_SetString(PyExc_RuntimeError, "invalid parameters");
    return NULL;
  }
  return PyCObject_FromVoidPtr((void*)NU_mean_function, NULL);
}

static PyObject *
py_method_median(PyObject *obj, PyObject *args) {
  if (not PyArg_ParseTuple(args, "")) {
    PyErr_SetString(PyExc_RuntimeError, "invalid parameters");
    return NULL;
  }
  return PyCObject_FromVoidPtr((void*)NU_median_function, NULL);
}

static PyObject *
py_method_minmax(PyObject *obj, PyObject *args) {
  unsigned nmin = 0;
  unsigned nmax = 0;
  if (not PyArg_ParseTuple(args, "II", &nmin, &nmax)) {
    PyErr_SetString(PyExc_RuntimeError, "invalid parameters");
    return NULL;
  }

  unsigned* funcdata = (unsigned*)malloc(2 * sizeof(unsigned));

  funcdata[0] = nmin;
  funcdata[1] = nmax;

  return PyCObject_FromVoidPtrAndDesc((void*)NU_minmax_function, funcdata, NU_destructor_function);
}

static PyObject *
py_method_sigmaclip(PyObject *obj, PyObject *args) {
  double low = 0.0;
  double high = 0.0;
  if (not PyArg_ParseTuple(args, "dd", &low, &high)) {
    PyErr_SetString(PyExc_RuntimeError, "invalid parameters");
    return NULL;
  }

  double *funcdata = (double*)malloc(2 * sizeof(double));

  funcdata[0] = low;
  funcdata[1] = high;

  return PyCObject_FromVoidPtrAndDesc((void*)NU_sigmaclip_function, funcdata, NU_destructor_function);
}
static PyObject *
py_method_quantileclip(PyObject *obj, PyObject *args) {
  double fclip = 0.0;
  if (not PyArg_ParseTuple(args, "d", &fclip)) {
    PyErr_SetString(PyExc_RuntimeError, "invalid parameters");
    return NULL;
  }

  if (fclip < 0 || fclip > 0.4) {
    PyErr_SetString(PyExc_ValueError, "invalid parameter fclip");
    return NULL;
  }

  double *funcdata = (double*)malloc(sizeof(double));
  *funcdata = fclip;
  return PyCObject_FromVoidPtrAndDesc((void*)NU_quantileclip_function, funcdata, NU_destructor_function);
}

static PyMethodDef combine_methods[] = {
    {"generic_combine", (PyCFunction) py_generic_combine, METH_VARARGS, ""},
    {"mean_method", (PyCFunction) py_method_mean, METH_VARARGS, ""},
    {"median_method", (PyCFunction) py_method_median, METH_VARARGS, ""},
    {"minmax_method", (PyCFunction) py_method_minmax, METH_VARARGS, ""},
    {"sigmaclip_method", (PyCFunction) py_method_sigmaclip, METH_VARARGS, ""},
    {"quantileclip_method", (PyCFunction) py_method_quantileclip, METH_VARARGS, ""},
    { NULL, NULL, 0, NULL } /* sentinel */
};

PyMODINIT_FUNC init_combine(void)
{
  PyObject *m;
  m = Py_InitModule3("_combine", combine_methods, combine__doc__);
  import_array();

  if (m == NULL)
    return;

  if (CombineError == NULL)
  {
    /*
     * A different base class can be used as base of the exception
     * passing something instead of NULL
     */
    CombineError = PyErr_NewException("_combine.CombineError", NULL, NULL);
  }
  Py_INCREF(CombineError);
  PyModule_AddObject(m, "CombineError", CombineError);
}
