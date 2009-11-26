/*
 * Copyright 2008-2009 Sergio Pascual
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

/* $Id$ */

#include <cstddef>

void method_mean(const double* data, size_t size,
		double* results[3], void* params)
{
  if (size == 0)
  {
    *results[0] = *results[1] = 0.0;
    *results[2] = 0;
    return;
  }

  if (size == 1)
  {
    *results[0] = data[0];
    *results[1] = 0.0;
    *results[2] = 1;
    return;
  }

  // Degrees of freedom
  const double dof = 0;

  double sum = 0.0;
  double sum2 = 0.0;

  for (size_t i = 0; i < size; ++i)
  {
    sum += data[i];
    sum2 += data[i] * data[i];
  }

  *results[0] = sum / size;
  *results[2] = size;
  *results[1] = sum2 / (size - dof) - (sum * sum) / (size * (size - dof));
}


